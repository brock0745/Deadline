import discord
from discord import app_commands
from discord.ext import tasks
import datetime
import re
import json
import os
import asyncio
from http.server import HTTPServer, BaseHTTPRequestHandler
import threading

# ------------------------------------------------------------------
# 設定エリア
# ------------------------------------------------------------------
# Renderの「Environment Variables」で設定したTOKENを読み込みます
# ローカルでテストする場合は、ここを直接 "トークン文字列" に書き換えても動きます
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")

# データを保存するファイル名
# 【重要】Renderの無料プランでは、再起動時にこのファイルはリセット(消滅)します。
# 永続化したい場合はGoogleスプレッドシート連携やデータベースが必要です。
DATA_FILE = "tasks.json"

# ------------------------------------------------------------------
# Botの初期化
# ------------------------------------------------------------------
intents = discord.Intents.default()
intents.message_content = True

class TaskBot(discord.Client):
    def __init__(self):
        super().__init__(intents=intents)
        self.tree = app_commands.CommandTree(self)

    async def setup_hook(self):
        await self.tree.sync()
        check_reminders.start()

client = TaskBot()

# ------------------------------------------------------------------
# クラウド常時稼働用 Webサーバー機能 (Keep Alive)
# ------------------------------------------------------------------
class HealthCheckHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        """アクセスが来たら 200 OK を返す"""
        self.send_response(200)
        self.send_header('Content-type', 'text/plain; charset=utf-8')
        self.end_headers()
        self.wfile.write(b"Bot is active and running!")

    def log_message(self, format, *args):
        # ログ出力を抑制
        return

def run_server():
    """Webサーバーを起動する"""
    # Renderなどのクラウド環境が指定するポートを使用。なければ8080。
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), HealthCheckHandler)
    print(f"🌍 Webサーバーがポート {port} で起動しました")
    server.serve_forever()

def keep_alive():
    """別スレッドでWebサーバーを開始する"""
    t = threading.Thread(target=run_server)
    t.daemon = True
    t.start()

# ------------------------------------------------------------------
# ユーティリティ関数
# ------------------------------------------------------------------
def parse_duration(duration_str: str):
    total_seconds = 0
    # 日
    days = re.search(r'(\d+)\s*(?:d|day|日)', duration_str)
    if days: total_seconds += int(days.group(1)) * 86400
    # 時間
    hours = re.search(r'(\d+)\s*(?:h|hour|時間)', duration_str)
    if hours: total_seconds += int(hours.group(1)) * 3600
    # 分
    minutes = re.search(r'(\d+)\s*(?:m|min|分)', duration_str)
    if minutes: total_seconds += int(minutes.group(1)) * 60

    if total_seconds == 0: return None
    return datetime.timedelta(seconds=total_seconds)

def load_tasks():
    if not os.path.exists(DATA_FILE): return []
    try:
        with open(DATA_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except: return []

def save_tasks(tasks_data):
    with open(DATA_FILE, 'w', encoding='utf-8') as f:
        json.dump(tasks_data, f, indent=4, ensure_ascii=False)

# ------------------------------------------------------------------
# スラッシュコマンド
# ------------------------------------------------------------------
@client.tree.command(name="add_task", description="課題の通知を登録します")
@app_commands.describe(
    task_name="課題の名前",
    deadline="締切日時 (例: 2024-05-20 23:59)",
    notify_before="通知タイミング (例: 1日, 3時間, 1日2時間)"
)
async def add_task(interaction: discord.Interaction, task_name: str, deadline: str, notify_before: str):
    try:
        fmt_deadline = deadline.replace("/", "-").replace(":", ":")
        deadline_dt = datetime.datetime.strptime(fmt_deadline, "%Y-%m-%d %H:%M")
    except ValueError:
        await interaction.response.send_message("⚠️ 日付形式エラー: `YYYY-MM-DD HH:MM` で入力してください。", ephemeral=True)
        return

    delta = parse_duration(notify_before)
    if delta is None:
        await interaction.response.send_message("⚠️ 時間指定エラー: `1日` `3時間` `30分` のように入力してください。", ephemeral=True)
        return

    notify_dt = deadline_dt - delta
    now = datetime.datetime.now()

    if notify_dt < now:
        await interaction.response.send_message("⚠️ 通知時間が過去です。", ephemeral=True)
        return

    new_task = {
        "user_id": interaction.user.id,
        "channel_id": interaction.channel_id,
        "task_name": task_name,
        "deadline_str": deadline_dt.strftime("%Y-%m-%d %H:%M"),
        "notify_at_iso": notify_dt.isoformat(),
        "original_notify_str": notify_before
    }

    current_tasks = load_tasks()
    current_tasks.append(new_task)
    save_tasks(current_tasks)

    await interaction.response.send_message(
        f"✅ 登録: **{task_name}**\n締切: {deadline_dt.strftime('%m/%d %H:%M')}\n通知: {notify_dt.strftime('%m/%d %H:%M')} ({notify_before}前)"
    )

@client.tree.command(name="list_tasks", description="自分の課題一覧")
async def list_tasks(interaction: discord.Interaction):
    tasks_data = load_tasks()
    user_tasks = [t for t in tasks_data if t["user_id"] == interaction.user.id]
    if not user_tasks:
        await interaction.response.send_message("登録なし", ephemeral=True)
        return
    
    msg = "**📋 課題一覧**\n"
    for t in user_tasks:
        msg += f"・**{t['task_name']}** (締切: {t['deadline_str']})\n"
    await interaction.response.send_message(msg, ephemeral=True)

# ------------------------------------------------------------------
# 定期実行タスク
# ------------------------------------------------------------------
@tasks.loop(seconds=60)
async def check_reminders():
    tasks_data = load_tasks()
    if not tasks_data: return

    now = datetime.datetime.now()
    remaining_tasks = []
    
    for task in tasks_data:
        notify_time = datetime.datetime.fromisoformat(task["notify_at_iso"])
        
        if now >= notify_time:
            try:
                channel = client.get_channel(task["channel_id"])
                if channel:
                    await channel.send(
                        f"<@{task['user_id']}> 🔔 **{task['task_name']}** の締切が **{task['original_notify_str']}前** です！\n(締切: {task['deadline_str']})"
                    )
            except Exception as e:
                print(f"Error: {e}")
        else:
            remaining_tasks.append(task)

    if len(tasks_data) != len(remaining_tasks):
        save_tasks(remaining_tasks)

@client.event
async def on_ready():
    print(f'ログインしました: {client.user}')

if __name__ == "__main__":
    # Webサーバー起動
    keep_alive()
    client.run(TOKEN)