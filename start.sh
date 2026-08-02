#!/bin/bash

echo "正在启动A股分析Agent系统..."
echo ""

# 检查Python是否安装
if ! command -v python3 &> /dev/null; then
    echo "错误: 未安装Python3，请先安装Python 3.8+"
    exit 1
fi

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "创建虚拟环境..."
    python3 -m venv venv
fi

# 激活虚拟环境
source venv/bin/activate

# 安装依赖
echo "检查依赖..."
pip install -q -r requirements.txt

echo ""
echo "启动模式选择:"
echo "1. 完整服务"
echo "2. 仅命令行交互"
echo "3. 仅生成报告"
echo "4. 仅启动监控"
echo ""
read -p "请选择 (1-4, 默认1): " choice

case $choice in
    1|'')
        python main.py --mode full
        ;;
    2)
        python main.py --cli
        ;;
    3)
        python main.py --report
        ;;
    4)
        python main.py --monitor
        ;;
    *)
        python main.py
        ;;
esac
