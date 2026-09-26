#!/usr/bin/env bash
# 出表的全部步骤. GitHub Actions (.github/workflows/update.yml) 与本地跑的是同一份,
# 工作流只另外负责存取 Gradle 缓存与提交.
#
# 用法: scripts/ci.sh <步骤>...
#   plan      看要不要重出: Bangumi 导出换了, 或 izuko-tv 的判据版本 (SEQUEL_SEASON_RULES) 变了; 输出 due=0/1.
#             只取两个小文件 (导出的 latest.json 与 izuko-tv 的 SequelSeasons.kt), 不下导出、不构建
#   download  取 Bangumi 最新导出 (同名的已下载过就跳过)
#   prepare   从导出取出出表器的输入 (scripts/prepare.py)
#   izuko     取 izuko-tv (IZUKO_REF), 拷入出表器
#   jdk       装带 JCEF 的 JBR (版本写死在下面, 从 JetBrains 的 CDN 取并校验, 不经 GitHub API); 已装就跳过
#   build     构建并运行出表器, 写 bgm-sequel-seasons.tsv
#   local     本地验证: download prepare izuko jdk build 依次跑 (不看 plan), 不提交
#
# 环境变量:
#   WORK       工作目录; 默认 $RUNNER_TEMP/work, 本地为仓库下的 .work
#   IZUKO_REF  用 izuko-tv 的哪个分支/标签 (默认 main)
#   IZUKO_REPO 从哪取 izuko-tv (默认 GitHub 上的; 本地验证还没推上去的改动时可以指向本地仓库)
#   FORCE      true = 导出与判据版本都没变也重出
#   JAVA_HOME  带 JCEF 的 JBR 21 (izuko-tv 的构建要 JetBrains 厂商, 桌面端代码要 JCEF 的类); 用 jdk 步骤装的就不用设
set -euo pipefail

ROOT=$(cd "$(dirname "$0")/.." && pwd)
if [ -z "${WORK:-}" ]; then
  if [ -n "${RUNNER_TEMP:-}" ]; then WORK="$RUNNER_TEMP/work"; else WORK="$ROOT/.work"; fi
fi
mkdir -p "$WORK"
IZUKO_REF=${IZUKO_REF:-main}
IZUKO_REPO=${IZUKO_REPO:-https://github.com/GrahamZen/izuko-tv.git}
TABLE="$ROOT/bgm-sequel-seasons.tsv"
RULES_FILE=app/shared/app-data/src/commonMain/kotlin/data/recommendation/SequelSeasons.kt

JBR_FILE=jbrsdk_jcef-21.0.11-linux-x64-b1163.116.tar.gz
JBR_DIR=${JBR_DIR:-$HOME/jbr/${JBR_FILE%.tar.gz}}

# 给后续 GitHub 步骤用的输出/环境变量; 本地跑时只打印
gh_output() { echo "$1"; if [ -n "${GITHUB_OUTPUT:-}" ]; then echo "$1" >> "$GITHUB_OUTPUT"; fi; }
gh_env() { if [ -n "${GITHUB_ENV:-}" ]; then echo "$1" >> "$GITHUB_ENV"; fi; }

# 表头里的某一项 (rules / dump / max_id); 没有表时为空
header_field() { [ -f "$TABLE" ] && head -1 "$TABLE" | grep -oE "(^| )$1=[^ ]+" | head -1 | cut -d= -f2 || true; }

step_plan() {
  curl -fsSL https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json -o "$WORK/latest.json"
  local dump rules
  dump=$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["name"])' "$WORK/latest.json")
  rules=$(curl -fsSL "https://raw.githubusercontent.com/GrahamZen/izuko-tv/$IZUKO_REF/$RULES_FILE" 2>/dev/null |
    grep -oE 'SEQUEL_SEASON_RULES = [0-9]+' | grep -oE '[0-9]+$' || true)
  echo "导出 $dump (表里 $(header_field dump)); 判据版本 ${rules:-无} (表里 $(header_field rules))"
  local due=0
  if [ -z "$rules" ]; then
    echo "izuko-tv ($IZUKO_REF) 还没有续作换季的公用代码, 不出表"
  elif [ "${FORCE:-}" = "true" ] || [ "$(header_field dump)" != "$dump" ] || [ "$(header_field rules)" != "$rules" ]; then
    due=1
  fi
  gh_output "due=$due"
}

step_download() {
  curl -fsSL https://raw.githubusercontent.com/bangumi/Archive/master/aux/latest.json -o "$WORK/latest.json"
  local url name digest
  read -r url name digest < <(python3 -c 'import json,sys; d=json.load(open(sys.argv[1])); print(d["browser_download_url"], d["name"], d.get("digest") or "-")' "$WORK/latest.json")
  if [ -f "$WORK/dump.zip" ] && [ "$(cat "$WORK/dump.name" 2>/dev/null)" = "$name" ]; then
    echo "导出 $name 已下载"
  else
    curl -fsSL "$url" -o "$WORK/dump.zip"
    if [ "$digest" != "-" ]; then
      echo "${digest#sha256:}  $WORK/dump.zip" | sha256sum -c -
    fi
    echo "$name" > "$WORK/dump.name"
  fi
}

step_prepare() {
  python3 "$ROOT/scripts/prepare.py" --dump "$WORK/dump.zip" --work "$WORK"
}

step_izuko() {
  local dir="$WORK/izuko-tv"
  rm -rf "$dir"
  git clone -q --depth 1 --branch "$IZUKO_REF" "$IZUKO_REPO" "$dir"
  git -C "$dir" rev-parse HEAD > "$WORK/izuko.sha"
  gh_env "IZUKO_SHA=$(cat "$WORK/izuko.sha")"
  cp "$ROOT/runner/BgmSequelSeasonsRunner.kt" "$dir/app/shared/app-data/src/desktopTest/kotlin/data/network/"
  {
    echo "ani.enable.firebase=false"
    echo "jvm.toolchain.version=21"
    echo "kotlin.native.ignoreDisabledTargets=true"
  } > "$dir/local.properties"
  echo "izuko-tv: $(cat "$WORK/izuko.sha") ($IZUKO_REF)"
}

step_jdk() {
  if [ ! -x "$JBR_DIR/bin/java" ]; then
    local tmp
    tmp=$(mktemp -d)
    curl -fsSL "https://cache-redirector.jetbrains.com/intellij-jbr/$JBR_FILE" -o "$tmp/$JBR_FILE"
    curl -fsSL "https://cache-redirector.jetbrains.com/intellij-jbr/$JBR_FILE.checksum" -o "$tmp/checksum"
    (cd "$tmp" && sha512sum -c checksum)
    mkdir -p "$JBR_DIR"
    tar -xzf "$tmp/$JBR_FILE" -C "$JBR_DIR" --strip-components=1
    rm -rf "$tmp"
  fi
  "$JBR_DIR/bin/java" -version 2>&1 | head -2
  gh_env "JAVA_HOME=$JBR_DIR"
  if [ -n "${GITHUB_PATH:-}" ]; then echo "$JBR_DIR/bin" >> "$GITHUB_PATH"; fi
}

step_build() {
  if [ -z "${JAVA_HOME:-}" ] && [ -x "$JBR_DIR/bin/java" ]; then export JAVA_HOME="$JBR_DIR"; fi
  (
    cd "$WORK/izuko-tv"
    export BGM_SEQUEL_WORK="$WORK"
    export BGM_SEQUEL_OUT="$TABLE"
    ./gradlew :app:shared:app-data:desktopTest --tests "*BgmSequelSeasonsRunner*" \
      --init-script "$ROOT/runner/runner.init.gradle" \
      "-Dorg.gradle.jvmargs=-Xmx4g -Dfile.encoding=UTF-8" \
      "-Dkotlin.daemon.jvm.options=-Xmx3g"
  )
  echo "$(($(wc -l < "$TABLE") - 1)) 部有候选季 ($(header_field dump), rules=$(header_field rules), izuko-tv $(cut -c1-9 "$WORK/izuko.sha"))" > "$WORK/summary.txt"
  cat "$WORK/summary.txt"
}

[ $# -gt 0 ] || { sed -n '2,24p' "$0"; exit 2; }
for s in "$@"; do
  case "$s" in
    plan | download | prepare | izuko | jdk | build) echo "== $s"; "step_$s" ;;
    local) for t in download prepare izuko jdk build; do echo "== $t"; "step_$t"; done ;;
    *) echo "未知步骤: $s" >&2; exit 2 ;;
  esac
done
