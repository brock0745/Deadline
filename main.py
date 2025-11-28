import discord
from discord import app_commands
from discord.ext import tasks
import datetime
import re
import json
import os
import keep_alive  # さっき作った keep_alive.py を読み込む

# ------------------------------------------------------------------
# 設定エリア
# ------------------------------------------------------------------
TOKEN = os.environ.get("DISCORD_BOT_TOKEN", "YOUR_BOT_TOKEN_HERE")
DATA_FILE = "tasks.json"

# 日本時間 (JST) の定義
JST = datetime.timezone(datetime.timedelta(hours=9), 'JST')

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
        if not check_reminders.is_running():
            check_reminders.start()

client = TaskBot()

# ------------------------------------------------------------------
# ユーティリティ関数
# ------------------------------------------------------------------
def parse_duration(duration_str: str):
    total_seconds = 0
    days = re.search(r'(\d+)\s*(?:d|day|日)', duration_str)
    if days: total_seconds += int(days.group(1)) * 86400
    hours = re.search(r'(\d+)\s*(?:h|hour|時間)', duration_str)
    if hours: total_seconds += int(hours.group(1)) * 3600
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
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    now = datetime.datetime.now(JST)

    try:
        fmt_deadline = deadline.replace("/", "-").replace(":", ":")
        deadline_dt = datetime.datetime.strptime(fmt_deadline, "%Y-%m-%d %H:%M").replace(tzinfo=JST)
    except ValueError:
        try:
            await interaction.followup.send("⚠️ 日付形式エラー: `YYYY-MM-DD HH:MM` で入力してください。")
        except: pass
        return

    delta = parse_duration(notify_before)
    if delta is None:
        try:
            await interaction.followup.send("⚠️ 時間指定エラー: `1日` `3時間` `30分` のように入力してください。")
        except: pass
        return

    notify_dt = deadline_dt - delta

    if notify_dt < now:
        try:
            await interaction.followup.send(f"⚠️ 通知時間が過ぎています。\n現在時刻(JST): {now.strftime('%m/%d %H:%M')}\n通知予定: {notify_dt.strftime('%m/%d %H:%M')}")
        except: pass
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

    try:
        await interaction.followup.send(
            f"✅ 登録: **{task_name}**\n締切: {deadline_dt.strftime('%m/%d %H:%M')}\n通知: {notify_dt.strftime('%m/%d %H:%M')} ({notify_before}前)"
        )
    except: pass

@client.tree.command(name="list_tasks", description="自分の課題一覧")
async def list_tasks(interaction: discord.Interaction):
    try:
        await interaction.response.defer(ephemeral=True)
    except discord.errors.NotFound:
        return

    tasks_data = load_tasks()
    user_tasks = [t for t in tasks_data if t["user_id"] == interaction.user.id]
    if not user_tasks:
        try:
            await interaction.followup.send("登録なし")
        except: pass
        return
    
    msg = "**📋 課題一覧**\n"
    for t in user_tasks:
        msg += f"・**{t['task_name']}** (締切: {t['deadline_str']})\n"
    
    try:
        await interaction.followup.send(msg)
    except: pass

# ------------------------------------------------------------------
# 定期実行タスク
# ------------------------------------------------------------------
@tasks.loop(seconds=60)
async def check_reminders():
    tasks_data = load_tasks()
    if not tasks_data: return

    now = datetime.datetime.now(JST)
    remaining_tasks = []
    
    for task in tasks_data:
        try:
            notify_time = datetime.datetime.fromisoformat(task["notify_at_iso"])
            if notify_time.tzinfo is None:
                 notify_time = notify_time.replace(tzinfo=JST)

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
        except:
            remaining_tasks.append(task)

    if len(tasks_data) != len(remaining_tasks):
        save_tasks(remaining_tasks)

@client.event
async def on_ready():
    print(f'ログインしました: {client.user} (JST mode)', flush=True)

if __name__ == "__main__":
    # Webサーバー起動 (keep_alive.pyの関数を呼び出す)
    keep_alive.keep_alive()
    client.run(TOKEN)