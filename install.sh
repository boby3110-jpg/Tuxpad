#!/usr/bin/env bash
# Tuxpad インストーラ（ディストリビューション自動判別）
#
# やること:
#   1. /etc/os-release からディストロを判別し、パッケージマネージャごとに
#      「システム版 PySide6」「charset-normalizer」「Qt 実行時ライブラリ」を入れる
#   2. `python3 -m venv --system-site-packages .venv` を作り、開発依存(pytest 等)を入れる
#   3. 起動ラッパー(~/.local/bin/tuxpad)と .desktop をこの clone の絶対パスで生成する
#
# なぜシステム版 PySide6 なのか:
#   pip 版 PySide6 は Qt 一式を同梱しており、fcitx5 の Qt6 プラグインや KDE の
#   プラットフォームテーマとプライベート ABI が一致せず読み込めない。つまり
#   **日本語入力(IME)とネイティブファイルダイアログが使えなくなる**。日本語を
#   打てないと使いものにならないので、既定ではシステム版が見つからなければ中断する
#   （どうしても pip 版で動かしたいときだけ --allow-pip-pyside6）。
#
# 使い方:
#   ./install.sh                  # 通常のインストール（アプリ実行に必要なものだけ。sudo を数回求められる）
#   ./install.sh --dev            # 上記に加え、開発/テスト用の依存(pytest 等・QtTest)も入れる
#   ./install.sh --print-plan     # 何も変更せず、判別結果と導入予定パッケージだけ表示
#   ./install.sh --dry-run        # 実行するコマンドを表示するだけ（変更しない）
#   ./install.sh --no-desktop     # ランチャー/.desktop を作らない
#   ./install.sh --desktop-only   # ランチャー/.desktop だけ作り直す（clone を移動した後など）
#
# 主なオプション:
#   --allow-pip-pyside6   システム版 PySide6 が無いとき pip 版で代替する（IME は使えない）
#   --bin-dir DIR         ランチャーの設置先（既定: ~/.local/bin）
#   --desktop-dir DIR     .desktop の設置先（既定: ~/.local/share/applications）
#   --python BIN          使う python（既定: python3）
#   --os-release FILE     /etc/os-release の代わりに読むファイル（テスト用）
#   -h, --help            このヘルプ

set -euo pipefail

INSTALL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PRINT_PLAN=0
DRY_RUN=0
NO_DESKTOP=0
DESKTOP_ONLY=0
ALLOW_PIP_PYSIDE6=0
DEV=0
PYTHON_BIN="python3"
OS_RELEASE="/etc/os-release"
BIN_DIR="${HOME}/.local/bin"
DESKTOP_DIR="${HOME}/.local/share/applications"

# --help は**ファイル先頭の説明ブロックだけ**を出す。
# ファイル全体から `#` の行を拾うと、下のほうの実装コメントまで混ざって
# ヘルプが読めなくなる（実際そうなっていた）。1 行目の shebang を落とし、
# コメントでない行（＝説明ブロックの終わり）に当たったら止める。
usage() { sed -n -e '1d' -e '/^#/!q' -e 's/^# \{0,1\}//p' "${BASH_SOURCE[0]}"; }

while [ $# -gt 0 ]; do
  case "$1" in
    --print-plan) PRINT_PLAN=1 ;;
    --dry-run) DRY_RUN=1 ;;
    --no-desktop) NO_DESKTOP=1 ;;
    --desktop-only) DESKTOP_ONLY=1 ;;
    --allow-pip-pyside6) ALLOW_PIP_PYSIDE6=1 ;;
    --dev) DEV=1 ;;
    --bin-dir) BIN_DIR="${2:?--bin-dir にはディレクトリが必要}"; shift ;;
    --desktop-dir) DESKTOP_DIR="${2:?--desktop-dir にはディレクトリが必要}"; shift ;;
    --python) PYTHON_BIN="${2:?--python には実行ファイルが必要}"; shift ;;
    --os-release) OS_RELEASE="${2:?--os-release にはファイルが必要}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "不明な引数: $1（--help を参照）" >&2; exit 2 ;;
  esac
  shift
done

# --bin-dir / --desktop-dir に相対パスが来ても絶対パスに直す。
# 相対のままだと .desktop の Exec= が相対パスになり、デスクトップ環境は
# 別の作業ディレクトリから起動するので**メニューに出るのに開けない**。
# しかも desktop-file-validate は素通りする（Exec= には PATH から引く
# コマンド名も書けるので、相対パスと見分けが付かない）。
# まだ存在しないディレクトリを指定できる必要があるので realpath -e は使わず、
# 自前で $PWD を前置する。
to_abs_dir() {
  local dir="$1"
  case "$dir" in
    /*) ;;
    *) dir="${PWD}/${dir#./}" ;;
  esac
  # 末尾の / を落とす（"${BIN_DIR}/tuxpad" が // にならないように）。
  # ただし "/" そのものは残す。
  while [ "${#dir}" -gt 1 ] && [ "${dir%/}" != "$dir" ]; do dir="${dir%/}"; done
  printf '%s' "$dir"
}
BIN_DIR="$(to_abs_dir "$BIN_DIR")"
DESKTOP_DIR="$(to_abs_dir "$DESKTOP_DIR")"

LAUNCHER_PATH="${BIN_DIR}/tuxpad"
DESKTOP_PATH="${DESKTOP_DIR}/tuxpad.desktop"
ICON_PATH="${INSTALL_DIR}/src/editor_app/resources/icon-256.png"
VENV_DIR="${INSTALL_DIR}/.venv"

# ---------------------------------------------------------------- ディストロ判別

# os-release の ID / ID_LIKE から「族」を決める。
# 出力: debian | fedora | arch | suse | unknown
detect_family() {
  local file="$1" id="" id_like="" token
  if [ -r "$file" ]; then
    # shellcheck disable=SC1090
    id="$(. "$file" >/dev/null 2>&1; printf '%s' "${ID:-}")"
    id_like="$(. "$file" >/dev/null 2>&1; printf '%s' "${ID_LIKE:-}")"
  fi
  for token in $id $id_like; do
    case "$token" in
      debian|ubuntu|linuxmint|pop|elementary|raspbian) echo debian; return ;;
      fedora|rhel|centos|rocky|almalinux) echo fedora; return ;;
      arch|archlinux|manjaro|endeavouros) echo arch; return ;;
      opensuse|opensuse-tumbleweed|opensuse-leap|suse|sles|sled) echo suse; return ;;
    esac
  done
  echo unknown
}

detect_id() {
  local file="$1"
  [ -r "$file" ] || { printf 'unknown'; return; }
  # shellcheck disable=SC1090
  ( . "$file" >/dev/null 2>&1; printf '%s' "${ID:-unknown}" )
}

# openSUSE はパッケージ名に Python のバージョンが入る（python313-pyside6 など）
python_tag() {
  "$PYTHON_BIN" -c 'import sys; print("python%d%d" % sys.version_info[:2])' 2>/dev/null || echo python3
}

FAMILY="$(detect_family "$OS_RELEASE")"
DISTRO_ID="$(detect_id "$OS_RELEASE")"

PKG_MANAGER=""
INSTALL_CMD=""
UPDATE_CMD=""
PYSIDE_PKGS=()
EXTRA_PKGS=()
RUNTIME_PKGS=()
# 開発/テスト時だけ入れる OS パッケージ（--dev のときのみ）。
# 分割パッケージの Debian でのみ中身がある（他ディストロは全部入り）。
DEV_PKGS=()
# パッケージ名の裏取り状況（正直に出す）。yes のときは下の「未了」警告を出さない:
#   debian 系 … Debian 13 実機で導入・offscreen pytest まで確認済み(2026-08)。
#                加えて CI/クラウド(Ubuntu 24.04)で apt により実在を確認している
#   fedora   … Fedora 44 実機で導入・PySide6動作・IME まで確認済み(2026-08)
#   arch     … EndeavourOS(Arch) 実機で導入・PySide6動作まで確認済み(2026-08)
#   suse     … 命名規則ベース。開発機(openSUSE)で実運用しているが、install.sh
#                そのものを流した確認は未了なので "no" のまま（警告を出す）
PKG_NAMES_VERIFIED="no"

case "$FAMILY" in
  debian)
    PKG_MANAGER="apt"
    INSTALL_CMD="sudo apt-get install -y"
    UPDATE_CMD="sudo apt-get update"
    # Debian/Ubuntu の PySide6 はモジュールごとに分割されている。
    # QtNetwork は単一インスタンス化(ipc.py)で使うので必須。
    PYSIDE_PKGS=(
      python3-pyside6.qtcore
      python3-pyside6.qtgui
      python3-pyside6.qtwidgets
      python3-pyside6.qtnetwork
    )
    EXTRA_PKGS=(python3-charset-normalizer python3-venv)
    RUNTIME_PKGS=(libegl1 libgl1 libxkbcommon0 libdbus-1-3 libfontconfig1 libxcb-cursor0)
    # QtTest は requirements-dev.txt の pytest-qt が import する。openSUSE 等の
    # 全部入りパッケージと違い Debian は分割なので、テストを回すには個別に要る
    # （アプリ本体の実行には不要。--dev のときだけ入れる）。
    DEV_PKGS=(python3-pyside6.qttest)
    PKG_NAMES_VERIFIED="yes"   # Debian 13 実機で検証済み(2026-08)
    ;;
  fedora)
    PKG_MANAGER="dnf"
    INSTALL_CMD="sudo dnf install -y"
    # 先に一度だけメタデータを取得し、存在確認は -C（キャッシュのみ）で行う。
    # こうしないと毎回ミラーへ再検証しに行き、遅いミラーで固まることがある。
    UPDATE_CMD="sudo dnf makecache"
    PYSIDE_PKGS=(python3-pyside6)
    EXTRA_PKGS=(python3-charset-normalizer)
    RUNTIME_PKGS=(mesa-libEGL libglvnd-glx libxkbcommon dbus-libs fontconfig xcb-util-cursor)
    PKG_NAMES_VERIFIED="yes"   # Fedora 44 実機で検証済み(2026-08)
    ;;
  arch)
    PKG_MANAGER="pacman"
    INSTALL_CMD="sudo pacman -S --needed --noconfirm"
    PYSIDE_PKGS=(pyside6)
    EXTRA_PKGS=(python-charset-normalizer)
    RUNTIME_PKGS=(libxkbcommon xcb-util-cursor fontconfig)
    PKG_NAMES_VERIFIED="yes"   # EndeavourOS(Arch) 実機で検証済み(2026-08)
    ;;
  suse)
    PKG_MANAGER="zypper"
    INSTALL_CMD="sudo zypper install -y"
    PYSIDE_PKGS=("$(python_tag)-pyside6")
    EXTRA_PKGS=("$(python_tag)-charset-normalizer")
    RUNTIME_PKGS=(libgthread-2_0-0 libxkbcommon0 libxcb-cursor0 fontconfig)
    ;;
esac

ALL_PKGS=("${PYSIDE_PKGS[@]}" "${EXTRA_PKGS[@]}" "${RUNTIME_PKGS[@]}")
if [ "$DEV" -eq 1 ] && [ "${#DEV_PKGS[@]}" -gt 0 ]; then
  ALL_PKGS+=("${DEV_PKGS[@]}")
fi

if [ "$PRINT_PLAN" -eq 1 ]; then
  echo "distro_id=${DISTRO_ID}"
  echo "family=${FAMILY}"
  echo "pkg_manager=${PKG_MANAGER}"
  echo "install_cmd=${INSTALL_CMD}"
  echo "pkg_names_verified=${PKG_NAMES_VERIFIED}"
  echo "pyside_packages=${PYSIDE_PKGS[*]-}"
  echo "extra_packages=${EXTRA_PKGS[*]-}"
  echo "runtime_packages=${RUNTIME_PKGS[*]-}"
  echo "dev_packages=${DEV_PKGS[*]-}"
  echo "install_dir=${INSTALL_DIR}"
  echo "venv=${VENV_DIR}"
  echo "launcher=${LAUNCHER_PATH}"
  echo "desktop=${DESKTOP_PATH}"
  echo "icon=${ICON_PATH}"
  exit 0
fi

# ---------------------------------------------------------------- 実行ヘルパ

run() {
  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] $*"
  else
    echo "  \$ $*"
    "$@"
  fi
}

# パッケージがそのディストロに存在するか。存在しないものを install に渡すと
# apt/dnf は「1 つでも無いと全部やらない」ので、先に落としておく。
package_exists() {
  local pkg="$1"
  case "$PKG_MANAGER" in
    apt)    apt-cache show "$pkg" >/dev/null 2>&1 ;;
    # dnf5 の `dnf info` は追加メタデータを引くため実機で数分かかることがある。
    # さらに素の `repoquery` は毎回ミラーへ再検証しに行き、遅いミラーで固まる
    # ことがある。UPDATE_CMD(makecache)で先に取得済みなので、ここは -C
    # （キャッシュのみ・ネットに出ない）で高速・確実に存在確認する。
    dnf)    [ -n "$(dnf -q -C repoquery "$pkg" 2>/dev/null)" ] ;;
    pacman) pacman -Si "$pkg" >/dev/null 2>&1 ;;
    zypper) zypper --non-interactive info "$pkg" >/dev/null 2>&1 ;;
    *)      return 1 ;;
  esac
}

python_has_modules() {
  "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import PySide6.QtWidgets, PySide6.QtGui, PySide6.QtCore, PySide6.QtNetwork  # noqa: F401
PY
}

# ---------------------------------------------------------------- テンプレート生成

# 埋め込む値（clone の絶対パス・--bin-dir）に**スペースや記号**が入ると、
# 生成物は黙って壊れる。しかも生成物ごとに「何を守るべきか」が違うので、
# 引用は 3 種類に分けてある。ここを 1 つにまとめてはいけない。
#
#   ランチャー(.sh) … "..." の中に置く   → \ " $ ` を守る（$b が展開されて消える）
#   .desktop の Exec= … コマンド行として読まれる
#                       → スペース等があれば "..." で囲む（囲まないと
#                         「~/my apps/bin/tuxpad」が 2 語に割れて、
#                         **メニューに出るのに押しても開かない**）
#   .desktop の値全般 … \ は \\ と書く決まり（freedesktop の string 型）
#
# なお `desktop-file-validate` はこの割れ方を通してしまう（Exec= には引数を
# 書けるので、割れているのか意図した引数なのか区別が付かない）。検証を
# 通ったからといって起動できるとは限らない。

# シェルスクリプトの "..." の中に置くための引用。
sh_dquote_escape() {
  local s="$1"
  s="${s//\\/\\\\}"   # \ を先に（後続で足す \ を二重に潰さないため）
  s="${s//\"/\\\"}"
  s="${s//\$/\\\$}"
  s="${s//\`/\\\`}"
  printf '%s' "$s"
}

# .desktop の値（string 型）として書くための引用。
desktop_value_escape() {
  local s="$1"
  s="${s//\\/\\\\}"
  s="${s//$'\t'/\\t}"
  s="${s//$'\n'/\\n}"
  s="${s//$'\r'/\\r}"
  printf '%s' "$s"
}

# .desktop の Exec= に「1 語」として置くための引用。
# 予約文字が無ければそのまま（実機で検証済みの見た目を変えないため）、
# あれば freedesktop の規則どおり "..." で囲んでから string 型の引用をかける。
desktop_exec_quote() {
  local s="$1"
  case "$s" in
    *[' '$'\t'$'\n''"'"'"'\'\>\<\~\|\&\;\$\*\?\#\(\)\`]*)
      s="${s//\\/\\\\}"
      s="${s//\"/\\\"}"
      s="${s//\`/\\\`}"
      s="${s//\$/\\\$}"
      s="\"${s}\""
      ;;
  esac
  desktop_value_escape "$s"
}

# テンプレートを展開して $dst を作る。
# 3 つめ以降の引数は "__NAME__=値" の並び。値は**呼び出し側で生成物に合わせて
# 引用済み**のものを渡すこと（どの引用が要るかはテンプレートの種類で決まり、
# ここからは分からないため）。
render_template() {
  local src="$1" dst="$2"
  shift 2
  [ -r "$src" ] || { echo "テンプレートが見つからない: $src" >&2; exit 1; }
  local content
  content="$(cat "$src")"

  # テンプレートが要求するプレースホルダを全部渡しているか（渡し忘れの検知）。
  # 見るのは**展開前**のテンプレート——展開後を見ると、パス自体に __ が
  # 入っていたときに誤検知するため。
  local want given found
  for want in $(printf '%s\n' "$content" | grep -o '__[A-Z_]\{2,\}__' | sort -u); do
    found=0
    for given in "$@"; do
      if [ "${given%%=*}" = "$want" ]; then found=1; break; fi
    done
    if [ "$found" -eq 0 ]; then
      echo "テンプレートに値を渡していないプレースホルダがある: $src の $want" >&2
      exit 1
    fi
  done

  local pair name value
  for pair in "$@"; do
    name="${pair%%=*}"
    value="${pair#*=}"
    # パターンも置換文字列も必ず " で囲む。囲まないと値の中の `&` が
    # 「一致した文字列」に化ける（bash 5.2 以降）。
    content="${content//"$name"/"$value"}"
  done

  if [ "$DRY_RUN" -eq 1 ]; then
    echo "  [dry-run] $src → $dst を生成"
    return
  fi
  mkdir -p "$(dirname "$dst")"
  printf '%s\n' "$content" > "$dst"
  echo "  生成: $dst"
}

install_desktop_files() {
  echo "[ランチャーと .desktop]"
  render_template "${INSTALL_DIR}/packaging/tuxpad-launcher.sh.in" "$LAUNCHER_PATH" \
    "__INSTALL_DIR__=$(sh_dquote_escape "$INSTALL_DIR")"
  [ "$DRY_RUN" -eq 1 ] || chmod +x "$LAUNCHER_PATH"
  render_template "${INSTALL_DIR}/packaging/tuxpad.desktop.in" "$DESKTOP_PATH" \
    "__LAUNCHER__=$(desktop_exec_quote "$LAUNCHER_PATH")" \
    "__ICON__=$(desktop_value_escape "$ICON_PATH")"
  if command -v update-desktop-database >/dev/null 2>&1; then
    run update-desktop-database "$DESKTOP_DIR"
  fi
  case ":${PATH}:" in
    *":${BIN_DIR}:"*) ;;
    *) echo "  注意: ${BIN_DIR} が PATH に入っていません。"
       echo "        端末から tuxpad で起動したい場合は PATH に追加してください。" ;;
  esac
}

# ---------------------------------------------------------------- 本体

if [ "$DESKTOP_ONLY" -eq 1 ]; then
  install_desktop_files
  echo "完了（--desktop-only）。"
  exit 0
fi

echo "== Tuxpad インストーラ =="
echo "  ディストロ: ${DISTRO_ID} (族: ${FAMILY})"
echo "  導入先:     ${INSTALL_DIR}"
echo

if [ "$FAMILY" = "unknown" ] || [ -z "$PKG_MANAGER" ]; then
  cat >&2 <<EOF
このディストリビューション (${DISTRO_ID}) は自動判別できませんでした。
pip 版 PySide6 で無理やり入れると日本語入力(IME)が使えなくなるため、中断します。

お手数ですが、次のものをディストロのパッケージマネージャで入れてから、
--desktop-only で仕上げだけ実行してください:

  * PySide6 (Qt6 版。QtCore / QtGui / QtWidgets / QtNetwork を含むもの)
  * charset-normalizer
  * Qt の実行時ライブラリ (libEGL / libGL / libxkbcommon / dbus / fontconfig /
    xcb-util-cursor 相当)

  $ ${PYTHON_BIN} -m venv --system-site-packages .venv
  $ .venv/bin/pip install -r requirements-dev.txt
  $ ./install.sh --desktop-only
EOF
  exit 1
fi

if [ "$DRY_RUN" -eq 0 ] && ! command -v "${PKG_MANAGER%% *}" >/dev/null 2>&1; then
  echo "${PKG_MANAGER} が見つかりません。判別 (${DISTRO_ID}) が誤っている可能性があります。" >&2
  exit 1
fi

if [ "$PKG_NAMES_VERIFIED" != "yes" ]; then
  echo "注意: ${FAMILY} 系のパッケージ名は実機での確認が未了です。"
  echo "      見つからないものは自動で読み飛ばし、最後に PySide6 が実際に"
  echo "      使えるかを確認します。"
  echo
fi

echo "[1/4] OS パッケージ"
if [ -n "$UPDATE_CMD" ] && [ "$DRY_RUN" -eq 0 ]; then
  # インデックスが古いと「存在するのに見つからない」になるので先に更新する
  run $UPDATE_CMD || echo "  （インデックス更新に失敗。古い情報のまま続行します）"
fi

TO_INSTALL=()
MISSING=()
for pkg in "${ALL_PKGS[@]}"; do
  if [ "$DRY_RUN" -eq 1 ] || package_exists "$pkg"; then
    TO_INSTALL+=("$pkg")
  else
    MISSING+=("$pkg")
  fi
done

if [ "${#MISSING[@]}" -gt 0 ]; then
  echo "  このディストロには無いパッケージ（読み飛ばします）: ${MISSING[*]}"
fi
if [ "${#TO_INSTALL[@]}" -gt 0 ]; then
  run $INSTALL_CMD "${TO_INSTALL[@]}"
else
  echo "  入れるものがありません。"
fi

echo
echo "[2/4] システム版 PySide6 の確認"
SYS_PYSIDE_OK=0
if [ "$DRY_RUN" -eq 1 ]; then
  echo "  [dry-run] ${PYTHON_BIN} で PySide6 を import できるか確認"
  SYS_PYSIDE_OK=1
elif python_has_modules; then
  echo "  OK: システム版 PySide6 が使えます（IME・ネイティブダイアログが有効）"
  SYS_PYSIDE_OK=1
elif [ "$ALLOW_PIP_PYSIDE6" -eq 1 ]; then
  cat <<EOF
  警告: システム版 PySide6 が見つかりませんでした。
        --allow-pip-pyside6 が指定されているので pip 版で続行します。
        ★この構成では日本語入力(IME)とネイティブファイルダイアログが使えません★
EOF
else
  cat >&2 <<EOF
  システム版 PySide6 が見つかりませんでした。

  このディストロのリポジトリに PySide6 (Qt6) が無いか、名前が想定と違います。
  例: Ubuntu 24.04 (noble) には python3-pyside6.* がありません
      （PySide2/Qt5 のみ。Debian 13 / Ubuntu 25.04 以降には入っています）。

  対処:
    * 新しめのリリースへ上げる、または PySide6 を提供するリポジトリを足す
    * 名前が違うだけなら、そのパッケージを手で入れてから本スクリプトを再実行
    * IME を諦めてでも動かしたい場合のみ: ./install.sh --allow-pip-pyside6
EOF
  exit 1
fi

echo
echo "[3/4] 仮想環境 (${VENV_DIR})"
if [ ! -d "$VENV_DIR" ]; then
  run "$PYTHON_BIN" -m venv --system-site-packages "$VENV_DIR"
else
  echo "  既にあります（作り直しません）"
fi
run "${VENV_DIR}/bin/pip" install --upgrade --quiet pip
if [ "$SYS_PYSIDE_OK" -eq 1 ] && [ "$ALLOW_PIP_PYSIDE6" -eq 0 ]; then
  # --system-site-packages なので、システム版がある PySide6 と charset-normalizer は
  # venv には入らない。既定ではアプリ実行に追加の pip 依存は無い（venv は空のまま
  # システム版を借りて動く）。--dev のときだけテスト用(pytest 等)を入れる。
  if [ "$DEV" -eq 1 ]; then
    run "${VENV_DIR}/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements-dev.txt"
  fi
else
  # pip 版 PySide6 で動かす場合は実行時依存(requirements.txt)を venv に入れる。
  run "${VENV_DIR}/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements.txt"
  if [ "$DEV" -eq 1 ]; then
    run "${VENV_DIR}/bin/pip" install --quiet -r "${INSTALL_DIR}/requirements-dev.txt"
  fi
fi

echo
echo "[4/4] 仕上げ"
if [ "$NO_DESKTOP" -eq 1 ]; then
  echo "  --no-desktop のため、ランチャーと .desktop は作りません。"
else
  install_desktop_files
fi

echo
echo "完了しました。"
echo "  起動:       ${LAUNCHER_PATH}   （または ${VENV_DIR}/bin/python ${INSTALL_DIR}/src/main.py）"
if [ "$DEV" -eq 1 ]; then
  echo "  テスト:     QT_QPA_PLATFORM=offscreen ${VENV_DIR}/bin/python -m pytest"
fi
if [ "$SYS_PYSIDE_OK" -eq 0 ] || [ "$ALLOW_PIP_PYSIDE6" -eq 1 ]; then
  echo "  ★pip 版 PySide6 のため、日本語入力(IME)は使えません★"
fi
