"""開いているファイルの外部変更を監視するミックスイン.

``MainWindow`` から「開いているファイルが、アプリの外で書き換わったときの
処理」だけを切り出したもの（`view_settings.py` と同じ切り出し方）。
別の PC・別のエディタ・同期ソフト (rclone/NAS) が同じファイルを書き換えた
ときに、黙って古い本文のままにせず、再読み込みを提案するためのもの。

流れは 4 段階になっている。

1. :meth:`FileWatchMixin._refresh_file_watches` … 開いているタブのパスを
   ``QFileSystemWatcher`` の監視対象と一致させる（開く・保存・タブを閉じる・
   タブがウィンドウ間を移動する、のたびに呼ぶ）
2. :meth:`FileWatchMixin._on_watched_file_changed` … 変更の通知を受け取り、
   保留リストに積んでデバウンス用のタイマーを起こす
3. :meth:`FileWatchMixin._process_pending_reloads` … デバウンス後に、
   溜まったパスをまとめて処理する
4. :meth:`FileWatchMixin._maybe_reload_changed_file` … ディスクの内容と
   本文を見比べて、本当に違うときだけ確認ダイアログを出す

**ネットワーク/FUSE 上のファイルだけは、上の 1〜2 が働かない**（引き継ぎ ⑭）。
``QFileSystemWatcher`` は Linux では inotify であり、**カーネルがこのマシンで
観測した変更しか拾えない**。NAS (CIFS) や rclone (FUSE) の向こう側で別の
マシンが書き換えても、その通知はこちらへ渡ってこない（SMB2 の CHANGE_NOTIFY は
Linux の cifs クライアントが inotify へ流さない）。そこで**自分から見に行く**
2 つの道を足してある。どちらも**新しい確認ダイアログは作らず、上の 2 の
入口（``_pending_reload_paths`` と ``_reload_check_timer``）へ合流する**。

- :meth:`FileWatchMixin._poll_network_files` … :data:`NETWORK_POLL_INTERVAL_MS`
  ごとに、ネットワーク上のタブの「見分け札」だけを確かめる
- 同じものを**ウィンドウがアクティブになった瞬間**にも 1 回走らせる
  （``MainWindow.changeEvent`` から呼ぶ。他のアプリから戻った直後に
  待たされないため）

**このモジュールを触るときの注意**（過去に実機で不具合として出たもの）:

- ``stat`` を増やさないこと。rclone/FUSE のような遅いファイルシステムでは
  ``stat`` 1 回が高くつき、「タブが増えるほどファイルを開くのが遅くなる」
  形で効いてしまう（`docs/performance-notes.md`）。**ポーリング
  (:meth:`FileWatchMixin._poll_network_files`) はこの戒めの唯一の例外**だが、
  (a) 同期的に開く処理の中ではなく**タイマーで間隔を空けて**走り、
  (b) **ネットワーク上のタブにしか** ``stat`` せず、
  (c) **ローカルのタブしか開いていなければタイマーごと止まる**
  （＝ ``stat`` は 1 回も走らない）形にしてある。
  見張りは `tests/test_network_file_polling.py`。
- 既に閉じたファイルの通知が遅れて届くことがある。開いているかどうかを
  必ず確かめてから扱うこと。
- **デバウンスの待ち時間のあいだにウィンドウが閉じられる**ことがある。
  閉じるときは :meth:`FileWatchMixin._cancel_pending_reloads` で保留を
  捨てること（``MainWindow.closeEvent`` から呼んでいる）。捨てないと、
  閉じたはずのウィンドウが後から確認ダイアログを出そうとする。
  **ポーリングのタイマーもそこで止めること**（同じ理由。止め忘れると
  閉じたウィンドウが 1 分後にダイアログを出す）。

``MainWindow`` にミックスインとして混ぜて使う。``self.tabs``・
``self.editors()``・``self.find_editor_by_path()`` に依存する。
"""

from __future__ import annotations

from pathlib import Path

# shiboken6 は PySide6 の土台（バインディングの実体）で、``PySide6.QtCore`` を
# import した時点で必ず一緒に読み込まれる。新しい依存ではないので
# requirements.txt に足す必要は無い（配布元の PySide6 でも同じ）。
import shiboken6
from PySide6.QtCore import QFileSystemWatcher, QTimer
from PySide6.QtWidgets import QMessageBox

from .dialogs import show_message_box
from .editor import EditorWidget, document_text
from .fileio import file_signature, read_text_file_detail, resolve_path

#: 開いているファイルが外部で変わってから、再読み込みの確認を出すまでの
#: 待ち時間 (ms)。連続した書き込みや temp→rename 方式の保存を 1 回に
#: まとめるため、少しだけ待つ（利用者の「1〜2 秒ほどで」に合わせる）。
RELOAD_CHECK_DELAY_MS = 1200

#: ネットワーク/FUSE 上のファイルを「自分から見に行く」間隔 (ms)（引き継ぎ ⑭）。
#:
#: inotify が届かないファイルシステムでは、これだけが外部変更に気づく道になる
#: （:meth:`FileWatchMixin._poll_network_files`）。利用者と相談した
#: 「30〜60 秒ごと」の真ん中を採った。**定数で切り出してあるのは、実機で
#: 重ければ伸ばせるようにするため**（設定メニューに出すかどうかは、まず
#: 実機で使ってみてから決める）。
#:
#: 1 回のポーリングで走る ``stat`` は「ネットワーク上のタブの数」だけ
#: （ローカルのタブは数に入らない）。タブ 20 枚でも 45 秒に 20 回なので、
#: CIFS の属性キャッシュ (``actimeo=5``) の内側に収まる。
NETWORK_POLL_INTERVAL_MS = 45_000

#: ``/proc/mounts`` の 3 列目（ファイルシステムの種別）のうち、
#: **ネットワーク越し**＝「サーバー側で起きた書き込みが inotify へ
#: 流れてこない」もの（引き継ぎ ⑭）。
#:
#: ここに無い種別は「ローカルディスク」として扱い、ポーリングしない
#: （``QFileSystemWatcher`` だけで足りる）。**迷ったら足す側に倒すこと**:
#: 足し忘れると「外部変更に気づかない」という実害が出るのに対し、
#: 余計に足したときの損は「そのタブを 45 秒に 1 回 ``stat`` する」だけ。
NETWORK_FS_TYPES = frozenset(
    {
        "cifs",
        "smbfs",
        "smb3",
        "nfs",
        "nfs4",
        "afs",
        "9p",
        "afpfs",
        "ceph",
        "glusterfs",
        "davfs",
    }
)


def _is_network_fs_type(fstype: str) -> bool:
    """``/proc/mounts`` の種別が「ネットワーク/FUSE 上」か。

    FUSE (``fuse``・``fuse.rclone`` など) も対象に含める。利用者の環境では
    rclone (``fuse.rclone``) で Google Drive をマウントしており、そちらも
    **リモート側の変更はやはり通知されない**ため。``fuseblk`` は除く
    （ntfs-3g のように
    **ローカルのブロックデバイス**を FUSE 経由で読むもので、書き込みは
    このマシンのカーネルを通る＝inotify が届く）。
    """
    return fstype in NETWORK_FS_TYPES or fstype == "fuse" or fstype.startswith("fuse.")


def _unescape_mount_field(field: str) -> str:
    r"""``/proc/mounts`` の 8 進エスケープを元に戻す。

    マウント点に空白を含むと ``/mnt/my\040disk`` のように書かれる
    （空白が列の区切りなので、そのままでは書けない）。``\040`` (空白)・
    ``\011`` (タブ)・``\012`` (改行)・``\134`` (``\`` そのもの) の 4 つが
    カーネルの対象。**``unicode_escape`` でまとめて復号しないこと**——
    あちらは ``\u`` なども解釈してしまい、日本語を含むマウント点
    （``/mnt/共有`` のような実在する形）を壊す。
    """
    if "\\" not in field:
        return field
    out: list[str] = []
    index = 0
    while index < len(field):
        char = field[index]
        if char == "\\":
            digits = field[index + 1 : index + 4]
            if len(digits) == 3 and all(digit in "01234567" for digit in digits):
                out.append(chr(int(digits, 8)))
                index += 4
                continue
        out.append(char)
        index += 1
    return "".join(out)


def read_mount_table(
    mounts_path: Path | str = "/proc/mounts",
) -> tuple[tuple[str, bool], ...]:
    """マウント点と「ネットワーク/FUSE 上か」の対を、**長い順**に並べて返す。

    長い順にしてあるのは、:func:`path_is_on_network_mount` が
    「いちばん長く前方一致するマウント点」で決めるため。``/`` が ext4 で
    ``/mnt/online`` が CIFS、というのが普通の形なので、短い方から見ると
    NAS 上のファイルまでローカル扱いになってしまう。

    読めなければ空を返す（判定は「全部ローカル」に倒れる＝今までどおりの
    動き）。``/proc`` が無い環境でも落ちないようにするため。
    """
    try:
        raw = Path(mounts_path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ()
    #: **同じマウント点が複数回出てきたら、後に書かれている方を採る。**
    #: Linux は同じ場所に重ねてマウントでき、見えるのは最後のものなので、
    #: ``/proc/mounts`` では後の行ほど実体に近い。`x-systemd.automount` を
    #: 付けた NAS はまさにこの形で 2 行出る（受け口の ``autofs`` が先、
    #: 実体の ``cifs`` が後）。先勝ちにすると **autofs を見てローカルと
    #: 判定し、NAS のファイルが見回りから外れる**（2026-10-19 実機で発生。
    #: 長さで並べ替えるだけでは、同じ長さなので出現順が残ってしまう）。
    latest: dict[str, bool] = {}
    for line in raw.splitlines():
        fields = line.split()
        if len(fields) < 3:
            continue
        latest[_unescape_mount_field(fields[1])] = _is_network_fs_type(fields[2])
    return tuple(
        sorted(latest.items(), key=lambda entry: len(entry[0]), reverse=True)
    )


#: :func:`read_mount_table` の結果の控え。**最初に要るときに 1 回読むだけ**で、
#: 以後は読み直さない（マウントは滅多に変わらないのに、タブを開くたびに
#: ``/proc/mounts`` を読むのは無駄）。``None`` は「まだ読んでいない」。
_mount_table_cache: tuple[tuple[str, bool], ...] | None = None


def mount_table(*, refresh: bool = False) -> tuple[tuple[str, bool], ...]:
    """控えてあるマウント表（初回だけ ``/proc/mounts`` を読む）。"""
    global _mount_table_cache
    if refresh or _mount_table_cache is None:
        _mount_table_cache = read_mount_table()
    return _mount_table_cache


def path_is_on_network_mount(path: Path | str) -> bool:
    """``path`` がネットワーク/FUSE 上のマウントにあるか。

    **いちばん長く前方一致するマウント点**の種別で決める。これにより
    「NAS の中にローカルを重ねてマウントしている」場合も、内側は正しく
    ローカルと見なせる（逆もまた同じ）。

    ``stat`` は 1 回も使わない（文字列の前方一致だけ）。ファイルを開く
    経路から呼ばれるため、ここで ``stat`` すると
    `docs/performance-notes.md` の不具合をそのまま繰り返すことになる。
    """
    text = str(path)
    for mount_point, is_network in mount_table():
        if text == mount_point or text.startswith(mount_point.rstrip("/") + "/"):
            return is_network
    return False


class FileWatchMixin:
    """開いているファイルの外部変更を監視して再読み込みを提案する。"""

    def _setup_file_watching(self) -> None:
        """監視用の ``QFileSystemWatcher`` とタイマーを用意する。

        ``MainWindow.__init__`` から、タブ (``self.tabs``) を作ったあとに
        1 度だけ呼ぶこと。
        """
        self._file_watcher = QFileSystemWatcher(self)
        self._file_watcher.fileChanged.connect(self._on_watched_file_changed)
        self._reload_check_timer = QTimer(self)
        self._reload_check_timer.setSingleShot(True)
        self._reload_check_timer.setInterval(RELOAD_CHECK_DELAY_MS)
        self._reload_check_timer.timeout.connect(self._process_pending_reloads)
        #: 変更を検知したが、まだ確認ダイアログを出していないファイルのパス。
        self._pending_reload_paths: set[str] = set()
        #: 再読み込みの確認ダイアログを表示中のパス（多重表示を防ぐ）。
        self._reload_prompt_active: set[str] = set()
        #: 途中で Qt へ制御を戻す長い処理（全置換など）の実行中かどうか。
        #: 立っている間は再読み込みの確認を出さず、後回しにする
        #: （:meth:`_process_pending_reloads` を参照）。
        self._long_operation_active = False
        #: ネットワーク/FUSE 上のファイルを自分から見に行くタイマー
        #: （引き継ぎ ⑭）。**ネットワーク上のタブが 1 枚も無ければ止まって
        #: いる**ので、ローカルだけで使っている間は ``stat`` が 1 回も
        #: 走らない（:meth:`_refresh_network_polling`）。
        self._network_poll_timer = QTimer(self)
        self._network_poll_timer.setInterval(NETWORK_POLL_INTERVAL_MS)
        self._network_poll_timer.timeout.connect(self._poll_network_files)
        #: 利用者が「再読み込みしますか？」に「いいえ」と答えたときの、
        #: そのときのディスクの見分け札（パス → 札）。
        #:
        #: **ポーリングを黙らせるためだけのもの。** 監視 (inotify) は
        #: 「変わった瞬間」に 1 回来るので断れば終わりだが、ポーリングは
        #: 「今のディスクが本文と違う」ことを根拠にするため、断っても
        #: :data:`NETWORK_POLL_INTERVAL_MS` ごとに同じ確認を出し続けて
        #: しまう。断られた状態を覚えておき、**その状態のままなら訊き直さない**。
        #:
        #: **:meth:`~editor_app.editor.EditorWidget.remember_disk_state` で
        #: 代わりにしてはいけない。** あちらは「自分はこのディスクの状態を
        #: 知っている」という札で、保存直前のチェック
        #: (`file_commands._check_external_change`) が使っている。断っただけで
        #: あの札を進めると、**他のマシンの編集を黙って上書きしてしまう**。
        self._declined_reload_signatures: dict[str, tuple[int, int] | None] = {}

    def _refresh_file_watches(self) -> None:
        """このウィンドウの開いているファイルのパスを、監視対象と一致させる。

        開く・保存・タブを閉じる・タブがウィンドウ間を移動する、などの
        タイミングで呼ぶ。存在するファイルだけを監視する（未保存の無題タブや、
        まだ書き出されていないパスは対象外）。temp→rename 方式の保存で
        監視が外れたパスも、ここで在れば再登録される。

        **``is_file()`` を呼ぶのは「これから監視に足すパス」だけにしてある**
        （パフォーマンス対応、2026-08-08）。以前は開いている全タブに対して
        毎回 ``is_file()`` していたため、ファイルを 1 つ開くたびに
        「タブの数 × 2 回」の stat が走っていた（このメソッドは
        ``_attach_editor`` と ``open_path`` の両方から呼ばれる）。
        rclone/FUSE のように stat 1 回が遅いファイルシステムでは、これが
        「タブが増えるほどファイルを開くのが遅くなる」形で効いてしまう。
        既に監視できているパスは ``QFileSystemWatcher`` 自身が面倒を見て
        いるので、こちらから改めて存在を確かめ直す必要はない。
        """
        wanted = {
            str(editor.path) for editor in self.editors() if editor.path is not None
        }
        current = set(self._file_watcher.files())
        to_remove = current - wanted
        # 監視されていないパスだけ「実在するか」を確かめる。無題タブのように
        # まだ書き出されていないパスと、外部で消されたファイルはここで落ちる。
        to_add = [path for path in (wanted - current) if Path(path).is_file()]
        if to_remove:
            self._file_watcher.removePaths(list(to_remove))
        if to_add:
            self._file_watcher.addPaths(to_add)
        # inotify が届かないファイルシステムのタブについては、ここで
        # ポーリングの要否も見直す（開く・保存・閉じる・窓間の移動という
        # 呼ばれ方がそのまま「対象が変わりうる瞬間」と一致している）。
        self._refresh_network_polling()

    # ------------------------------------------------------------------
    # ネットワーク/FUSE 上のファイルを自分から見に行く（引き継ぎ ⑭）
    # ------------------------------------------------------------------
    def _is_network_path(self, path: Path) -> bool:
        """このパスが inotify の届かない場所（ネットワーク/FUSE 上）か。

        **メソッドにしてあるのはテストから差し替えられるようにするため**
        （ヘッドレスの CI に NAS は無いので、「このパスはネットワーク上」と
        偽装できないと、この機能はまったく見張れない）。中身は
        :func:`path_is_on_network_mount` そのまま。
        """
        return path_is_on_network_mount(path)

    def _refresh_network_polling(self) -> None:
        """ポーリングのタイマーを、今開いているタブに合わせて入り切りする。

        **ネットワーク上のタブが 1 枚も無ければタイマーごと止める。**
        ローカルディスクは ``QFileSystemWatcher`` で足りているので、
        ポーリングするだけ無駄（しかも ``stat`` は遅い環境では高くつく）。
        ここで止めておくおかげで、**ローカルだけで使っている利用者には
        この機能による ``stat`` が 1 回も増えない**。
        """
        open_paths = {
            str(editor.path) for editor in self.editors() if editor.path is not None
        }
        # 閉じたタブについて覚えている「断られた札」は捨てる（開き直したら
        # もう一度確認してよい。持ち越すと、開き直した後の 1 回目を黙らせる）。
        for path in list(self._declined_reload_signatures):
            if path not in open_paths:
                del self._declined_reload_signatures[path]
        wanted = any(self._is_network_path(Path(path)) for path in open_paths)
        if wanted:
            if not self._network_poll_timer.isActive():
                self._network_poll_timer.start()
        elif self._network_poll_timer.isActive():
            self._network_poll_timer.stop()

    def _poll_network_files(self) -> None:
        """ネットワーク上のタブを ``stat`` し、変わっていれば既存の道へ流す。

        タイマーからと、**ウィンドウがアクティブになった瞬間**
        (``MainWindow.changeEvent``) の 2 か所から呼ばれる。

        比べるのは :func:`~editor_app.fileio.file_signature` の「見分け札」
        ``(更新日時 ns, サイズ)`` と、そのタブが最後に見た札
        (:attr:`~editor_app.editor.EditorWidget.disk_signature`)。
        **札はタブが開いた直後・保存した直後・読み直した直後に既に
        控えてある**ので、この機能のために新しく控える場所を作る必要は無く、
        ``stat`` もここでの 1 回だけで済む。

        違っていたら :attr:`_pending_reload_paths` に積んで
        :attr:`_reload_check_timer` を起こすだけ——つまり **inotify が
        届いたときとまったく同じ道**を通る（確認ダイアログも、未保存の
        タブでの差分の見せ方も、新しくは作らない）。
        """
        # Qt 側 (C++) が既に片付けられた後に発火することがある
        # （:meth:`_process_pending_reloads` と同じ理由）。
        if not shiboken6.isValid(self):
            return
        # 閉じたウィンドウでは何もしない。:meth:`_cancel_pending_reloads` が
        # タイマーを止めているので普段ここへは来ないが、**止め忘れや新しい
        # 呼び出し元が増えたときに「閉じたウィンドウが再読み込みを訊いてくる」
        # という実機の不具合へ直行する**ので、出す側でも確かめる
        # （デバウンスの側で実際に踏んだ形。今度は 45 秒おきに何度でも
        # 発火するぶん、壊れ方はそちらより悪い）。
        # ``isHidden()`` は**最小化では False**、閉じた・隠したときだけ True。
        # 最小化しているだけの利用者の見張りを黙らせないために、
        # ``isVisible()`` ではなくこちらで見る。
        if self.isHidden():
            return
        # 全置換のように「途中で Qt へ制御を戻す」処理の最中は見に行かない。
        # 次の発火でまた来るので、ここで諦めてよい。
        if self._long_operation_active:
            return
        found = False
        for editor in self.editors():
            path = editor.path
            if path is None or not self._is_network_path(path):
                continue
            signature = file_signature(path)
            if signature == editor.disk_signature:
                continue
            if signature == self._declined_reload_signatures.get(str(path)):
                # この状態での再読み込みは、利用者が既に断っている。
                continue
            self._pending_reload_paths.add(str(path))
            found = True
        if found:
            self._reload_check_timer.start()

    def _find_editor_for_watched_path(self, path: str) -> EditorWidget | None:
        """``QFileSystemWatcher`` から渡されたパスのタブを探す。

        監視に登録するのは :meth:`_refresh_file_watches` が作った
        ``str(editor.path)``（＝既に :func:`~editor_app.fileio.resolve_path` を
        通したパス）なので、まずはそのまま比較してみる。``Path.resolve()`` は
        パスの各階層に ``lstat`` するため、rclone/FUSE のような遅い
        ファイルシステムでは 1 回が高くつく。**そのままで見つからなかった
        ときだけ** resolve して探し直す（Qt 側がパスを正規化して返す
        可能性への保険）。
        """
        editor = self.find_editor_by_path(Path(path))
        if editor is not None:
            return editor
        return self.find_editor_by_path(resolve_path(path))

    def _on_watched_file_changed(self, path: str) -> None:
        """監視中のファイルが変わった（``QFileSystemWatcher.fileChanged``）。"""
        # そのファイルがこのウィンドウでもう開かれていないなら、何もしない。
        # 監視から外し、保留中の再読み込みも取り消す。
        # （閉じたファイルにまで再読み込みの確認が出た、という実機報告への対応。
        # QFileSystemWatcher は removePaths の直後でも、既にカーネルが積んだ
        # 変更イベントを届けてくることがあり、そのとき下の「まだ在れば監視し直す」
        # が閉じたファイルを再登録してしまっていた。ここで開いているかどうかを
        # 見て、開いていなければ確実に手を引く。）
        if self._find_editor_for_watched_path(path) is None:
            if path in self._file_watcher.files():
                self._file_watcher.removePaths([path])
            self._pending_reload_paths.discard(path)
            return

        # temp→rename 方式の保存では、元ファイルが置き換わって監視が外れる
        # ことがある。まだ開いていてファイルが在れば監視し直す。
        if Path(path).is_file() and path not in self._file_watcher.files():
            self._file_watcher.addPath(path)
        self._pending_reload_paths.add(path)
        self._reload_check_timer.start()

    def _cancel_pending_reloads(self) -> None:
        """保留中の再読み込みを全て取り消し、監視も解く。

        ウィンドウを閉じるときに :meth:`MainWindow.closeEvent` から呼ぶ。
        閉じたウィンドウが後から「再読み込みしますか？」を出さないように
        するため（デバウンスの待ち時間 (:data:`RELOAD_CHECK_DELAY_MS`) の
        あいだに閉じられると、タイマーだけが後に残る）。

        **ポーリングのタイマー (:attr:`_network_poll_timer`) も必ず止める**
        （引き継ぎ ⑭）。こちらは :data:`NETWORK_POLL_INTERVAL_MS` ＝ 45 秒
        おきに発火し続けるので、止め忘れると「閉じたウィンドウが 1 分後に
        再読み込みを訊いてくる」という、デバウンスのときと同じ形の不具合に
        なる（しかも一度きりではなく何度でも）。
        """
        self._reload_check_timer.stop()
        self._network_poll_timer.stop()
        self._pending_reload_paths.clear()
        self._declined_reload_signatures.clear()
        watched = self._file_watcher.files()
        if watched:
            self._file_watcher.removePaths(watched)

    def _process_pending_reloads(self) -> None:
        """デバウンス後。変更が来ていたファイルを 1 つずつ確認する。

        **先にこのウィンドウがまだ生きているかを確かめる**。Qt 側
        (C++) が既に片付けられた後にこのタイマーが発火することがあり、
        そのまま ``self.tabs`` に触ると
        ``Internal C++ object (QStackedWidget) already deleted`` で落ちる。
        :meth:`_cancel_pending_reloads` で閉じるときに止めてはいるが、
        閉じる経路を通らずに片付けられる場合（テストの後始末など）が
        あるため、発火した側でも一度確かめる。
        """
        if not shiboken6.isValid(self):
            return
        # 全置換のように「途中で Qt へ制御を戻しながら本文を書き換える」
        # 処理の最中は、確認を出さずに後回しにする。ここで本文を丸ごと
        # 入れ替えてしまうと、置換の残りが別の本文に対して走ることになる
        # （引き継ぎ ⑨ でバッチ化したときに初めて起こりうるようになった）。
        # 保留中のパスは消さずに残し、少し待ってからやり直す。
        if self._long_operation_active:
            self._reload_check_timer.start()
            return
        paths = list(self._pending_reload_paths)
        self._pending_reload_paths.clear()
        for path in paths:
            self._maybe_reload_changed_file(Path(path))

    def _maybe_reload_changed_file(self, path: Path) -> None:
        """外部で変わったファイルについて、必要なら確認して再読み込みする。

        - このウィンドウで開いていないファイルは無視する。
        - ファイルが（削除等で）読めない・書き込み途中で読めない場合は
          何もしない（次の変更イベントでまた来る）。
        - ディスクの内容が今の本文と同じなら、実質変わっていない
          （＝自分で保存した直後など）ので確認しない。
        - 内容が違うときだけ「再読み込みしますか？」を出す。未保存の編集が
          あるタブでも必ず確認する（勝手に上書きして編集を失わないため）。
        """
        # 先にタブを探す。見つかったタブが持つパスは必ず
        # :func:`~editor_app.fileio.resolve_path` を通してあるので、それを
        # そのまま正規化済みのパスとして使い回す
        # （パス解決のための余分な lstat を増やさないため）。
        editor = self._find_editor_for_watched_path(str(path))
        if editor is None or editor.path is None:
            return
        resolved = editor.path
        key = str(resolved)
        if key in self._reload_prompt_active:
            return
        if not resolved.is_file():
            return

        try:
            content = read_text_file_detail(resolved)
        except Exception:  # noqa: BLE001 - 書き込み途中等で読めないだけなら黙って見送る
            return

        text, encoding, newline = content.text, content.encoding, content.newline
        if text == document_text(editor):
            # 中身は本文と同じ（自分で保存した直後、外部ツールが同じ内容で
            # 書き直した等）。「見るべき変更は無い」とここで確定できるので、
            # 見分け札も今の状態に更新しておく。更新しないと、保存直前の
            # チェック (`file_commands._check_external_change`) だけが
            # 古い札を握ったままになり、**中身は同じなのに保存が止まる**。
            editor.remember_disk_state()
            self._declined_reload_signatures.pop(key, None)
            return

        self.tabs.setCurrentWidget(editor)
        if editor.is_modified:
            message = (
                f"「{editor.display_name}」が外部で変更されました。\n"
                "このタブには未保存の変更があります。再読み込みすると、"
                "その編集は破棄されます。\n\n再読み込みしますか？"
            )
        else:
            message = (
                f"「{editor.display_name}」が外部で変更されました。\n\n"
                "再読み込みしますか？"
            )

        self._reload_prompt_active.add(key)
        try:
            answer = show_message_box(
                self,
                QMessageBox.Icon.Question,
                "再読み込み",
                message,
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.Yes,
            )
        finally:
            self._reload_prompt_active.discard(key)

        if answer == QMessageBox.StandardButton.Yes:
            self._declined_reload_signatures.pop(key, None)
            self._reload_editor_content(
                editor, text, encoding, newline, alternatives=content.alternatives
            )
            return

        # 断られた。**この状態のディスクについては訊き直さない**
        # （引き継ぎ ⑭。ポーリングは「違っている」ことを根拠にするので、
        # 覚えておかないと 45 秒ごとに同じ確認を出し続ける）。
        # 覚えるのはポーリングを黙らせる札だけで、タブが知っている
        # ディスクの札 (``disk_signature``) は**進めない**——進めると
        # 保存直前のチェックが「外部変更は無い」と判断し、断っただけの
        # 利用者が他のマシンの編集を黙って上書きしてしまう。
        self._declined_reload_signatures[key] = file_signature(resolved)

    def _reload_editor_content(
        self,
        editor: EditorWidget,
        text: str,
        encoding: str,
        newline: str,
        *,
        alternatives: tuple[str, ...] = (),
    ) -> None:
        """エディタの本文をディスクの内容で置き換える（カーソル位置は保つ）。

        ``alternatives`` は「文字コードの判定が拮抗した」ときの別候補
        (:attr:`~editor_app.editor.EditorWidget.encoding_alternatives`)。
        **既定が空なのは意図的**——利用者が自分で文字コードを選んで
        開き直した場合は拮抗が解決しているので、印を消す側が既定になる。
        """
        cursor_position = editor.textCursor().position()
        editor.encoding = encoding
        editor.encoding_alternatives = alternatives
        editor.newline = newline
        editor.set_content(text)
        cursor = editor.textCursor()
        cursor.setPosition(min(cursor_position, len(text)))
        editor.setTextCursor(cursor)
        editor.ensureCursorVisible()
        # 読み直した以上、このタブが知っているディスクの状態も今の状態。
        # これがあるおかげで、監視が先に拾って読み直したファイルについては
        # 保存直前のチェックが重ねてダイアログを出すことはない。
        editor.remember_disk_state()
        # 保存し直しで監視が外れている場合の再登録。
        self._refresh_file_watches()
