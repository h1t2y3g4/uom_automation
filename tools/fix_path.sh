#!/bin/bash
# 自动更新文档中的项目根目录路径
# 用法：在项目任意目录下运行 bash tools/fix_path.sh

set -e

# 获取项目根目录
PROJECT_ROOT="$(git rev-parse --show-toplevel)"

if [ -z "$PROJECT_ROOT" ]; then
    echo "错误：无法获取项目根目录，请确保在 git 仓库内运行"
    exit 1
fi

echo "检测到项目根目录：$PROJECT_ROOT"

# 需要处理的文档列表
DOCS=(
    "SKILL.md"
    "README.md"
    "doc/HANDOFF_UOM_PROJECT.md"
)

UPDATED=0

for doc in "${DOCS[@]}"; do
    filepath="$PROJECT_ROOT/$doc"
    if [ ! -f "$filepath" ]; then
        echo "跳过：$doc（文件不存在）"
        continue
    fi

    # 匹配 `PROJECT_ROOT=` 的行，替换后面的路径
    if grep -qF 'PROJECT_ROOT=' "$filepath"; then
        sed -i 's|PROJECT_ROOT=[^`]*|PROJECT_ROOT='"$PROJECT_ROOT"'|' "$filepath"
        echo "已更新：$doc"
        UPDATED=$((UPDATED + 1))
    else
        echo "跳过：$doc（未找到 'PROJECT_ROOT=' 前缀）"
    fi
done

echo "完成，共更新 $UPDATED 个文件"
