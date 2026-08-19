#!/system/bin/sh

# 变量定义
box_dir="/data/adb/box"
box_run="${box_dir}/run"
box_pid="${box_run}/box.pid"

run_as_su() {
    su -c "$1"
}

stop_service() {
    echo "服务正在关闭..."
    run_as_su "${box_dir}/scripts/box.iptables disable"
    run_as_su "${box_dir}/scripts/box.service stop"
}

start_service() {
    echo "服务正在启动，请稍候..."
    run_as_su "${box_dir}/scripts/box.service start"
    run_as_su "${box_dir}/scripts/box.iptables enable"
}

if [ -f "${box_pid}" ]; then
    PID=$(cat "${box_pid}")
    if [ -e "/proc/${PID}" ]; then
        stop_service
    else
        start_service
    fi
else
    start_service
fi
