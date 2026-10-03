#!/usr/bin/env bash
set -euo pipefail

REPO="yelei1027/codex-engineering-harness-v3"
TAG="${1:-v3.1.1}"
VERSION="${TAG#v}"
ASSET="codex-engineering-harness-${VERSION}-linux-x86_64.tar.gz"
TARGET="${HARNESS_INSTALL_DIR:-$HOME/.local/share/codex-engineering-harness/releases/$VERSION}"
STATE="${HARNESS_STATE_DIR:-$HOME/.local/share/codex-engineering-harness}"

fail() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

[[ "$(uname -s)" == "Linux" ]] || fail "当前安装器仅验证 Linux。"
[[ "$(uname -m)" == "x86_64" ]] || fail "当前 Release 仅验证 x86_64。"

for cmd in gh tar sha256sum python3 npm uv docker; do
  command -v "$cmd" >/dev/null 2>&1 || fail "缺少依赖: $cmd"
done

docker compose version >/dev/null 2>&1 || fail "缺少 Docker Compose v2"
gh auth status >/dev/null 2>&1 || fail "请先执行: gh auth login"
if [[ -f "$STATE/accepted.json" ]]; then
  fail "检测到已有 Harness。不要覆盖安装；请按 README 的“更新”流程操作。"
fi

tmp="$(mktemp -d)"
stage="${TARGET}.new.$$"
trap 'rm -rf "$tmp" "$stage"' EXIT

printf 'Downloading %s %s...\n' "$REPO" "$TAG"
gh release download "$TAG" \
  --repo "$REPO" \
  --pattern "$ASSET*" \
  --dir "$tmp"

(
  cd "$tmp"
  sha256sum -c "$ASSET.sha256"
)

rm -rf "$stage"
mkdir -p "$stage"
tar -xzf "$tmp/$ASSET" -C "$stage"
src="$stage/codex-engineering-harness"
[[ -x "$src/bin/harness" ]] || fail "Release 内容不完整。"

mkdir -p "$(dirname "$TARGET")"
rm -rf "$TARGET"
mv "$src" "$TARGET"
cd "$TARGET"
./bin/harness check
python3 scripts/bootstrap.py --services
./bin/harness install

printf '\nHarness %s 安装完成。\n' "$VERSION"
printf '安装目录: %s\n' "$TARGET"
printf '下一步:\n'
printf '  cd "/你的项目路径"\n'
printf '  harness start .\n'
printf '然后在 Codex Desktop 将该项目作为主文件夹打开；L2 项目首次使用请运行 /hooks 并信任项目 Hook。\n'
printf '如果当前 shell 找不到 harness，请执行:\n'
printf '  export PATH="$HOME/.local/bin:$PATH"\n'
