import requests
import time
import smtplib
import threading
from datetime import datetime
from email.mime.text import MIMEText
import tkinter as tk
from tkinter import ttk, messagebox

# --------------------------
# 全局状态
# --------------------------
is_running = False
sent_tx_hashes = set()
last_price = 0

# --------------------------
# 邮件发送
# --------------------------
def send_email_alert(sender, auth_code, receiver, subject, content):
    try:
        msg = MIMEText(content, "plain", "utf-8")
        msg["From"] = sender
        msg["To"] = receiver
        msg["Subject"] = subject
        with smtplib.SMTP_SSL("smtp.qq.com", 465, timeout=30) as server:
            server.login(sender, auth_code)
            result = server.sendmail(sender, [receiver], msg.as_string())
            if result:
                return False, f"收件人被拒绝: {result}"
        return True, "发送成功"
    except smtplib.SMTPAuthenticationError as e:
        return False, f"邮箱认证失败，请检查授权码: {e}"
    except smtplib.SMTPRecipientsRefused as e:
        return False, f"收件人地址被拒绝: {e}"
    except smtplib.SMTPException as e:
        return False, f"邮件发送错误: {e}"
    except Exception as e:
        return False, f"未知错误: {e}"

# --------------------------
# API 状态检测
# --------------------------
def check_api_status():
    """检测所有 API 是否可用，返回状态信息"""
    status = {"price": False, "gas": False, "tx": False, "email": False}
    messages = []
    
    # 检测 ETH 价格 API
    try:
        url = "https://min-api.cryptocompare.com/data/price?fsym=ETH&tsyms=USDT"
        r = requests.get(url, timeout=5).json()
        if "USDT" in r:
            status["price"] = True
            messages.append("✅ ETH价格API正常")
        else:
            messages.append("❌ ETH价格API返回异常")
    except Exception as e:
        messages.append(f"❌ ETH价格API失败: {str(e)[:30]}")
    
    # 检测 Gas API
    try:
        url = "https://eth-mainnet.public.blastapi.io"
        payload = {"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1}
        r = requests.post(url, json=payload, timeout=8).json()
        if "result" in r:
            status["gas"] = True
            messages.append("✅ Gas价格API正常")
        else:
            messages.append("❌ Gas价格API返回异常")
    except Exception as e:
        messages.append(f"❌ Gas价格API失败: {str(e)[:30]}")
    
    # 检测大额转账 API
    try:
        url = "https://eth-mainnet.public.blastapi.io"
        payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        r = requests.post(url, json=payload, timeout=8).json()
        if "result" in r:
            status["tx"] = True
            messages.append("✅ 区块数据API正常")
        else:
            messages.append("❌ 区块数据API返回异常")
    except Exception as e:
        messages.append(f"❌ 区块数据API失败: {str(e)[:30]}")
    
    return status, messages

# --------------------------
# 获取数据
# --------------------------
def get_eth_price():
    try:
        # 使用 CryptoCompare API (国内可用)
        url = "https://min-api.cryptocompare.com/data/price?fsym=ETH&tsyms=USDT"
        return float(requests.get(url, timeout=5).json()["USDT"])
    except:
        try:
            # 备用: DeFi Llama API (国内可用)
            url = "https://coins.llama.fi/prices/current/coingecko:ethereum?searchWidth=4h"
            return float(requests.get(url, timeout=5).json()["coins"]["coingecko:ethereum"]["price"])
        except:
            return 0

def get_eth_gas():
    try:
        # 使用 Blast API 获取 gas 价格 (国内可用)
        url = "https://eth-mainnet.public.blastapi.io"
        payload = {"jsonrpc": "2.0", "method": "eth_gasPrice", "params": [], "id": 1}
        gas_wei = int(requests.post(url, json=payload, timeout=8).json()["result"], 16)
        gas_gwei = gas_wei / 1e9
        # RPC只返回当前gas价格，我们用它作为所有类型的参考
        # 调整: safe=基础, standard=1.2x, fast=1.5x
        # 注意：gas可能很低（如0.07Gwei），使用float保留精度
        return {
            "safe": round(gas_gwei, 2),
            "standard": round(gas_gwei * 1.2, 2),
            "fast": round(gas_gwei * 1.5, 2)
        }
    except:
        return {"safe": 0, "standard": 0, "fast": 0}

def get_large_tx():
    try:
        # 使用 Blast API 获取最新交易 (国内可用)
        url = "https://eth-mainnet.public.blastapi.io"
        # 获取最新区块
        payload = {"jsonrpc": "2.0", "method": "eth_blockNumber", "params": [], "id": 1}
        block_hex = requests.post(url, json=payload, timeout=8).json()["result"]
        block_num = int(block_hex, 16)
        # 获取区块详情
        payload = {"jsonrpc": "2.0", "method": "eth_getBlockByNumber", "params": [hex(block_num), True], "id": 1}
        block_data = requests.post(url, json=payload, timeout=8).json()["result"]
        res = []
        for tx in block_data.get("transactions", [])[:20]:
            # value 是 wei，转换为 ETH
            value_wei = int(tx.get("value", "0x0"), 16)
            value_eth = value_wei / 1e18
            if value_eth >= 100:
                res.append({
                    "from": tx.get("from", "")[:8] + "...",
                    "to": tx.get("to", "")[:8] + "...",
                    "amount": int(value_eth),
                    "hash": tx.get("hash", "")
                })
        return res[:5]
    except:
        return []

# --------------------------
# 监控主循环
# --------------------------
def monitor_loop(sender_email, auth_code, receiver_email):
    global is_running, last_price, sent_tx_hashes
    last_price = get_eth_price()
    update_status("运行中：监控ETH价格、Gas、大额转账")

    while is_running:
        now = datetime.now().strftime("%m-%d %H:%M:%S")
        alerts = []

        try:
            price = get_eth_price()
            if price != 0 and last_price != 0:
                change = abs(price - last_price) / last_price * 100
                if change >= 3.0:
                    alerts.append(f"📉 ETH价格波动超3% → {price:.2f} USDT")
            last_price = price

            gas = get_eth_gas()
            if gas["fast"] >= 30:
                alerts.append(f"⛽ Gas过高！快速={gas['fast']} Gwei")

            txs = get_large_tx()
            for tx in txs:
                if tx["hash"] not in sent_tx_hashes:
                    sent_tx_hashes.add(tx["hash"])
                    alerts.append(f"🐋 大额转账：{tx['amount']} ETH\n{tx['from']} → {tx['to']}")

            if alerts:
                title = "ETH 链上异常报警"
                content = f"时间：{now}\n\n" + "\n\n".join(alerts)
                success, msg = send_email_alert(sender_email, auth_code, receiver_email, title, content)
                if success:
                    log_text.insert(tk.END, f"[{now}] 🔴 已发邮件报警\n")
                else:
                    log_text.insert(tk.END, f"[{now}] ❌ 邮件发送失败: {msg}\n")
            else:
                log_text.insert(tk.END, f"[{now}] 🟢 正常\n")

            log_text.see(tk.END)

        except Exception as e:
            log_text.insert(tk.END, f"[{now}] ⚠️ 数据获取异常: {str(e)[:50]}\n")

        time.sleep(10)

    update_status("已停止 | 等待启动")

# --------------------------
# 更新状态栏
# --------------------------
def update_status(text):
    status_label.config(text=text)

# --------------------------
# 启动 / 停止
# --------------------------
def start_monitor():
    global is_running
    if is_running:
        messagebox.showwarning("提示", "监控已在运行")
        return

    sender = sender_entry.get().strip()
    auth = auth_entry.get().strip()
    receiver = receiver_entry.get().strip()

    if not sender or not auth or not receiver:
        messagebox.showerror("错误", "请填写完整邮箱信息")
        return

    # 显示启动检测
    log_text.insert(tk.END, "━━━ 启动前检测 ━━━\n")
    log_text.see(tk.END)
    root.update()

    # 检测 API 状态
    status, messages = check_api_status()
    for msg in messages:
        log_text.insert(tk.END, f"  {msg}\n")
    log_text.see(tk.END)
    root.update()

    # 测试邮件发送
    log_text.insert(tk.END, "  📧 测试邮件发送...\n")
    log_text.see(tk.END)
    root.update()
    
    success, msg = send_email_alert(sender, auth, receiver, "【ETH监控】启动测试", "ETH监控工具已启动，这是一封测试邮件。")
    if success:
        log_text.insert(tk.END, "  ✅ 测试邮件发送成功，请检查收件箱\n")
    else:
        log_text.insert(tk.END, f"  ❌ 测试邮件发送失败: {msg}\n")
        # 如果是收件人被拒绝，给出更具体的建议
        if "收件人地址被拒绝" in msg or "Mailbox unavailable" in msg:
            log_text.insert(tk.END, "  💡 提示: 收件邮箱可能有问题，请检查邮箱地址是否正确，或尝试换一个收件邮箱\n")
        elif "认证失败" in msg:
            log_text.insert(tk.END, "  💡 提示: 请检查QQ邮箱授权码是否正确\n")
    
    log_text.insert(tk.END, "━━━━━━━━━━━━━━━━━\n")
    log_text.see(tk.END)

    # 检查关键 API 是否可用
    if not status["price"]:
        log_text.insert(tk.END, "⚠️ 警告: ETH价格API不可用，价格监控功能受限\n")
    if not status["gas"]:
        log_text.insert(tk.END, "⚠️ 警告: Gas价格API不可用，Gas监控功能受限\n")
    if not status["tx"]:
        log_text.insert(tk.END, "⚠️ 警告: 区块数据API不可用，大额转账监控功能受限\n")

    log_text.insert(tk.END, "✅ 监控已启动\n")

    is_running = True
    threading.Thread(target=monitor_loop, args=(sender, auth, receiver), daemon=True).start()

def stop_monitor():
    global is_running
    is_running = False
    log_text.insert(tk.END, "🛑 监控已停止\n")
    update_status("已停止 | 等待启动")

# --------------------------
# GUI 界面
# --------------------------
root = tk.Tk()
root.title("ETH 实时监控工具")
root.geometry("650x620")

# 邮箱配置
frame = ttk.LabelFrame(root, text="邮箱配置")
frame.pack(pady=10, fill="x", padx=20)

ttk.Label(frame, text="发件邮箱(QQ)：").grid(row=0, column=0, padx=5, pady=5, sticky="w")
sender_entry = ttk.Entry(frame, width=40)
sender_entry.grid(row=0, column=1, padx=5, pady=5)

ttk.Label(frame, text="邮箱授权码：").grid(row=1, column=0, padx=5, pady=5, sticky="w")
auth_entry = ttk.Entry(frame, width=40, show="*")
auth_entry.grid(row=1, column=1, padx=5, pady=5)

ttk.Label(frame, text="接收邮箱：").grid(row=2, column=0, padx=5, pady=5, sticky="w")
receiver_entry = ttk.Entry(frame, width=40)
receiver_entry.grid(row=2, column=1, padx=5, pady=5)

# 按钮
btn_frame = ttk.Frame(root)
btn_frame.pack(pady=10)
ttk.Button(btn_frame, text="启动监控", command=start_monitor).grid(row=0, column=0, padx=10)
ttk.Button(btn_frame, text="停止监控", command=stop_monitor).grid(row=0, column=1, padx=10)

# 日志
log_frame = ttk.LabelFrame(root, text="运行日志")
log_frame.pack(pady=10, fill="both", expand=True, padx=20)
log_text = tk.Text(log_frame, height=20)
log_text.pack(side="left", fill="both", expand=True)
scroll = ttk.Scrollbar(log_frame, command=log_text.yview)
scroll.pack(side="right", fill="y")
log_text.config(yscrollcommand=scroll.set)

status_label = ttk.Label(root, text="就绪 | 请填写邮箱后启动监控", anchor=tk.W, relief=tk.SUNKEN)
status_label.pack(side=tk.BOTTOM, fill=tk.X, padx=2, pady=2)

disclaimer = ttk.Label(root, text="免责声明：本工具仅供学习参考，不构成投资建议 | 技术支持QQ：1745957645", anchor=tk.CENTER)
disclaimer.pack(side=tk.BOTTOM, fill=tk.X, padx=5, pady=3)

update_status("就绪 | 请填写邮箱后启动监控")
root.mainloop()