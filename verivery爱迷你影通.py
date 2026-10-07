import logging
import subprocess
import requests
import time
from datetime import datetime
import os
import pandas as pd
import threading

# ==================== 配置区 ====================
PRODUCT_SPECS = {
    "59098729": [   # 大陆地址
        {"param_id": "11652425", "name": "ANNA"},
        {"param_id": "11652426", "name": "ELLA"},
        {"param_id": "11652427", "name": "GAWON"},
        {"param_id": "11652428", "name": "NARIN"},
        {"param_id": "11652429", "name": "SOOIN"},
    ],
    "59098751": [   # 非大陆地址
        {"param_id": "11652436", "name": "ANNA"},
        {"param_id": "11652437", "name": "ELLA"},
        {"param_id": "11652438", "name": "GAWON"},
        {"param_id": "11652439", "name": "NARIN"},
        {"param_id": "11652440", "name": "SOOIN"},
    ],
}

PRODUCT_LABELS = {
    "59098729": "大陆地址",
    "59098751": "非大陆地址",
}

GITHUB_REPO = "Juineii/meovv_imini0621"
GITHUB_BRANCH = "main"
PUSH_INTERVAL = 60
# ========================================================

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s - %(levelname)s - %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    encoding='utf-8'
)

lines_since_last_push = 0
lines_lock = threading.Lock()
file_lock = threading.Lock()

monitor_items = []      # 最终监控列表
last_stock = {}         # 记录上次库存

# ---------- 获取单个规格库存（不变） ----------
def get_stock(pro_id, param_id):
    url = "https://www.imini.tv/mallorder/api/detail.php"
    headers = {
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "X-Requested-With": "XMLHttpRequest",
    }
    data = {"pro_id": pro_id, "param_id": param_id}
    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        json_data = response.json()
        if "data" in json_data and "pro_stock" in json_data["data"]:
            return int(json_data["data"]["pro_stock"])
        else:
            return None
    except Exception as e:
        logging.error(f"获取库存失败 pro_id={pro_id} param_id={param_id}: {e}")
        return None

# ---------- 写入成员专属 CSV ----------
def write_to_csv(timestamp, label, name, stock_change, single_sale):
    global lines_since_last_push
    csv_filename = f"{name}.csv"
    product_display = f"{label}-{name}"

    data = {
        '时间': timestamp,
        '商品名称': product_display,
        '库存变化': stock_change,
        '单笔销量': single_sale
    }

    with file_lock:
        if os.path.exists(csv_filename):
            df_existing = pd.read_csv(csv_filename, encoding='utf-8-sig')
        else:
            df_existing = pd.DataFrame(columns=['时间', '商品名称', '库存变化', '单笔销量'])

        new_row = pd.DataFrame([data])
        df_updated = pd.concat([df_existing, new_row], ignore_index=True)
        df_updated.to_csv(csv_filename, index=False, encoding='utf-8-sig')

    print(f"💾 数据已写入 {csv_filename}")

    with lines_lock:
        lines_since_last_push += 1

# ---------- Git 推送 ----------
def git_push_update():
    try:
        token = os.environ.get('GITHUB_TOKEN')
        if not token:
            token = "fansign"
            logging.warning("使用硬编码 Token，建议使用环境变量")

        remote_url = f"https://{token}@github.com/{GITHUB_REPO}.git"
        subprocess.run(['git', 'add', '*.csv'], check=True, capture_output=True, timeout=30)

        result = subprocess.run(['git', 'diff', '--cached', '--quiet'], capture_output=True, timeout=30)
        if result.returncode != 0:
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_msg = f"自动更新数据 {timestamp}"
            subprocess.run(['git', 'commit', '-m', commit_msg], check=True, capture_output=True, timeout=30)
            subprocess.run(
                ['git', 'push', remote_url, f'HEAD:{GITHUB_BRANCH}'],
                check=True,
                capture_output=True,
                text=True,
                timeout=30
            )
            print(f"✅ 已推送到 GitHub: {commit_msg}")
            return True
        else:
            print("⏭️  CSV 文件无变化，跳过推送")
            return True
    except Exception as e:
        logging.error(f"推送失败: {e}")
        return False

# ---------- 监控主循环 ----------
def monitor_stock(interval):
    global monitor_items, last_stock

    # 初始化监控项（从配置构建）
    for pro_id, specs in PRODUCT_SPECS.items():
        label = PRODUCT_LABELS.get(pro_id, pro_id)
        for spec in specs:
            param_id = spec["param_id"]
            name = spec["name"]
            # 获取初始库存
            stock = get_stock(pro_id, param_id)
            if stock is None:
                logging.warning(f"商品 {pro_id} 规格 {name}({param_id}) 初始库存获取失败，跳过此规格")
                continue
            item = {
                "pro_id": pro_id,
                "param_id": param_id,
                "name": name,
                "label": label
            }
            monitor_items.append(item)
            key = (pro_id, param_id)
            last_stock[key] = stock
            # 记录初始库存
            now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            write_to_csv(now, label, name, f"初始库存：{stock}", 0)
            print(f"{now} - {label}-{name}, 初始库存: {stock}")

    if not monitor_items:
        logging.error("没有可监控的规格，程序退出")
        return

    print(f"监控项列表: {monitor_items}")

    while True:
        # 轮询每个监控项
        for item in monitor_items:
            pro_id = item["pro_id"]
            param_id = item["param_id"]
            name = item["name"]
            label = item["label"]
            key = (pro_id, param_id)

            stock = get_stock(pro_id, param_id)
            if stock is None:
                continue

            now = datetime.now()
            time_str = now.strftime("%Y-%m-%d %H:%M:%S")

            if stock != last_stock[key]:
                diff = last_stock[key] - stock
                stock_change = f"{last_stock[key]} -> {stock}"
                single_sale = diff
                write_to_csv(time_str, label, name, stock_change, single_sale)
                print(f"{time_str} - {label}-{name}, 库存变化: {last_stock[key]} -> {stock} 销量: {diff}")

            last_stock[key] = stock

        time.sleep(interval)

# ---------- 后台推送线程 ----------
def push_worker():
    global lines_since_last_push
    while True:
        time.sleep(PUSH_INTERVAL)
        with lines_lock:
            pending = lines_since_last_push
        if pending > 0:
            print(f"⏰ 定时推送：有 {pending} 条新数据待推送")
            with file_lock:
                success = git_push_update()
            if success:
                with lines_lock:
                    lines_since_last_push = 0
                print("✅ 推送成功，计数器归零")
            else:
                print("⚠️ 推送失败，下次再试")

# ---------- 主程序 ----------
if __name__ == "__main__":
    push_thread = threading.Thread(target=push_worker, daemon=True)
    push_thread.start()

    try:
        monitor_stock(10)   # 每10秒检查一次
    except KeyboardInterrupt:
        print("\n监控程序被用户终止")
        with lines_lock:
            pending = lines_since_last_push
        if pending > 0:
            print(f"正在推送剩余的 {pending} 条数据...")
            with file_lock:
                success = git_push_update()
            if success:
                with lines_lock:
                    lines_since_last_push = 0
                print("✅ 剩余数据已推送")
            else:
                print("⚠️ 剩余数据推送失败，请手动检查")
        else:
            print("无待推送数据")