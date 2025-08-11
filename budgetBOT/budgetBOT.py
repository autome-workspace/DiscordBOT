import discord
from discord import ui
from discord.ext import commands
import json
import os
from datetime import datetime
import uuid

# --- Bot and File Configuration ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Configuration and Data File ---
TOKEN = ""
DATA_FILE = "F3RC_data.json"

# --- Load Configuration ---
try:
    with open("config.json", "r", encoding="utf-8") as config_file:
        config = json.load(config_file)
        TOKEN = config["TOKEN"]
except (FileNotFoundError, KeyError):
    print("エラー: config.json にTOKENが見つかりません。")
    exit()

# --- Data Helper Functions ---
def load_data():
    """F3RC_data.jsonからすべてのチームデータを読み込む"""
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_data(data):
    """すべてのチームデータをF3RC_data.jsonに保存する"""
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def get_team_by_channel_id(channel_id, data):
    """チャンネルIDから紐づくチームのデータを検索する"""
    for team_name, team_data in data.items():
        if team_data.get("settings", {}).get("channel_id") == channel_id:
            return team_name, team_data
    return None, None

# --- UI Classes (変更なし) ---
class AddItemModal(ui.Modal, title="品物をリストに追加"):
    def __init__(self, parent_view):
        super().__init__()
        self.parent_view = parent_view
    item_name = ui.TextInput(label="購入物", placeholder="例: USB Type-C ケーブル", required=True)
    link = ui.TextInput(label="参考リンク", placeholder="https://example.com/item", required=False)
    amount = ui.TextInput(label="金額（半角数字のみ）", placeholder="例: 1500", required=True)
    async def on_submit(self, interaction: discord.Interaction):
        try:
            amount_val = int(self.amount.value)
        except ValueError:
            await interaction.response.send_message("⚠️ 金額は半角数字で入力してください。", ephemeral=True)
            return
        new_item = {"name": self.item_name.value, "link": self.link.value, "amount": amount_val}
        self.parent_view.items.append(new_item)
        await self.parent_view.update_message(interaction)

class MultiItemRequestView(ui.View):
    def __init__(self, author: discord.User, team_name: str, team_data: dict):
        super().__init__(timeout=600)
        self.author = author
        self.items = []
        self.team_name = team_name
        self.team_data = team_data
    def create_embed(self):
        embed = discord.Embed(title=f"🛒 {self.team_name} 購入申請リスト", color=discord.Color.blue())
        embed.set_author(name=f"申請者: {self.author.display_name}", icon_url=self.author.display_avatar)
        total_amount = sum(item['amount'] for item in self.items)
        budget = self.team_data.get("settings", {}).get("budget", 0)
        if not self.items:
            embed.description = "まだ品物は追加されていません。\n「品物を追加」ボタンから入力してください。"
        else:
            description = ""
            for i, item in enumerate(self.items, 1):
                description += f"**{i}. {item['name']}** - ¥{item['amount']:,}\n"
                if item['link']:
                    description += f"   [リンク]({item['link']})\n"
            embed.description = description
            embed.add_field(name="合計金額", value=f"**¥{total_amount:,}**")
        embed.add_field(name="利用可能予算", value=f"¥{budget:,}", inline=False)
        embed.set_footer(text="内容を確認して「申請を提出」を押してください。")
        return embed
    async def update_message(self, interaction: discord.Interaction):
        self.children[1].disabled = not self.items
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("⚠️ この申請を操作できるのは申請者本人のみです。", ephemeral=True)
            return False
        return True
    @ui.button(label="品物を追加", style=discord.ButtonStyle.secondary, emoji="➕")
    async def add_item_button(self, interaction: discord.Interaction, button: ui.Button):
        modal = AddItemModal(parent_view=self)
        await interaction.response.send_modal(modal)
    @ui.button(label="申請を提出", style=discord.ButtonStyle.primary, emoji="🚀", disabled=True)
    async def submit_button(self, interaction: discord.Interaction, button: ui.Button):
        data = load_data()
        team_name, team_data = get_team_by_channel_id(interaction.channel_id, data)
        if not team_data:
            await interaction.response.send_message("⚠️ このチャンネルはチームに登録されていません。", ephemeral=True)
            return
        total_amount = sum(item['amount'] for item in self.items)
        current_budget = team_data.get("settings", {}).get("budget", 0)
        if total_amount > current_budget:
            await interaction.response.send_message(f"⚠️ **エラー:** 予算が不足しています。(残高: ¥{current_budget:,})", ephemeral=True)
            return
        request_id = str(uuid.uuid4())
        new_request = {
            "request_id": request_id, "applicant_id": self.author.id,
            "applicant_name": self.author.display_name, "status": "pending",
            "items": self.items, "total_amount": total_amount, "approved_amount": 0,
            "timestamp": datetime.now().isoformat(), "approver_id": None,
            "approver_name": None, "receipt": None, "collect": False, "refund": False
        }
        user_id_str = str(self.author.id)
        if "requests" not in data[team_name]: data[team_name]["requests"] = {}
        if user_id_str not in data[team_name]["requests"]: data[team_name]["requests"][user_id_str] = {}
        data[team_name]["requests"][user_id_str][request_id] = new_request
        save_data(data)
        final_embed = discord.Embed(title=f"【{team_name}】購入申請", description=f"申請ID: `{request_id}`", color=discord.Color.gold())
        final_embed.set_author(name=f"申請者: {self.author.display_name}", icon_url=self.author.display_avatar)
        item_description = ""
        for i, item in enumerate(self.items, 1):
            item_description += f"**{i}. {item['name']}** - ¥{item['amount']:,}\n"
            if item['link']: item_description += f"   [リンク]({item['link']})\n"
        final_embed.add_field(name="申請内容", value=item_description, inline=False)
        final_embed.add_field(name="💰 合計金額", value=f"**¥{total_amount:,}**", inline=True)
        final_embed.set_footer(text="ステータス: 審査中")
        await interaction.message.delete()
        approval_view = ApprovalView(request_id=request_id, team_name=team_name, applicant_id=self.author.id)
        await interaction.channel.send(embed=final_embed, view=approval_view)

class PartialApprovalView(ui.View):
    def __init__(self, original_view: 'ApprovalView'):
        super().__init__(timeout=300)
        self.original_view = original_view
        self.items = original_view.request_data['items']
        options = [discord.SelectOption(label=f"{item['name']} (¥{item['amount']:,})", value=str(i)) for i, item in enumerate(self.items)]
        self.item_select.options = options
        self.item_select.max_values = len(self.items)
    @ui.select(placeholder="承認する品物をすべて選択してください...")
    async def item_select(self, interaction: discord.Interaction, select: ui.Select):
        await interaction.response.defer()
    @ui.button(label="この内容で確定", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        if not self.item_select.values:
            await interaction.response.send_message("⚠️ 承認する品物を1つ以上選択してください。", ephemeral=True)
            return
        approved_indices = {int(v) for v in self.item_select.values}
        approved_items = [item for i, item in enumerate(self.items) if i in approved_indices]
        rejected_items = [item for i, item in enumerate(self.items) if i not in approved_indices]
        await self.original_view.finalize_approval(interaction, "partial", approved_items, rejected_items)

class ApprovalView(ui.View):
    def __init__(self, request_id: str, team_name: str, applicant_id: int):
        super().__init__(timeout=None)
        self.request_id = request_id
        self.team_name = team_name
        self.applicant_id = applicant_id
    async def load_request_data(self):
        data = load_data()
        self.team_data = data.get(self.team_name, {})
        user_id_str = str(self.applicant_id)
        self.request_data = self.team_data.get("requests", {}).get(user_id_str, {}).get(self.request_id)
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        await self.load_request_data()
        if not self.request_data:
            await interaction.response.send_message("⚠️ この申請データは見つかりませんでした。", ephemeral=True)
            return False
        if self.request_data['status'] != 'pending':
            await interaction.response.send_message("⚠️ この申請は既に処理されています。", ephemeral=True)
            return False
        role_id = self.team_data.get("settings", {}).get("role_id")
        if not role_id or not any(role.id == role_id for role in interaction.user.roles):
            await interaction.response.send_message("⚠️ この申請を審査する権限がありません。", ephemeral=True)
            return False
        return True
    async def finalize_approval(self, interaction: discord.Interaction, result: str, approved_items: list = None, rejected_items: list = None):
        data = load_data()
        team_data = data[self.team_name]
        user_id_str = str(self.applicant_id)
        request_data = team_data["requests"][user_id_str][self.request_id]
        approved_amount = sum(item['amount'] for item in approved_items) if approved_items else 0
        current_budget = team_data["settings"]["budget"]
        if approved_amount > current_budget:
            await interaction.response.send_message(f"⚠️ **エラー:** 承認額 (¥{approved_amount:,}) が予算の残高を超えています。", ephemeral=True)
            return
        if approved_amount > 0: team_data["settings"]["budget"] -= approved_amount
        request_data["status"] = "approved" if approved_amount > 0 else "rejected"
        request_data["approved_amount"] = approved_amount
        request_data["approver_id"] = interaction.user.id
        request_data["approver_name"] = interaction.user.display_name
        for item in request_data["items"]: item["approval"] = item in approved_items
        save_data(data)
        applicant_member = interaction.guild.get_member(self.applicant_id)
        final_embed = discord.Embed(title=f"審査結果: {self.team_name}", description=f"申請ID: `{self.request_id}`", color=discord.Color.dark_grey())
        final_embed.set_author(name=f"申請者: {applicant_member.display_name}", icon_url=applicant_member.display_avatar)
        description = ""
        if approved_items:
            description += "**✅ 承認された品物**\n"
            for item in approved_items: description += f"- {item['name']} (¥{item['amount']:,}) {item.get('link','')}\n"
        if rejected_items:
            description += "\n**❌ 却下された品物**\n"
            for item in rejected_items: description += f"- {item['name']} (¥{item['amount']:,}) {item.get('link','')}\n"
        final_embed.description = description
        footer_text = f"審査者: {interaction.user.display_name}"
        if approved_amount > 0: footer_text += f"\n予算から ¥{approved_amount:,} を支出 (新残高: ¥{team_data['settings']['budget']:,})"
        final_embed.set_footer(text=footer_text)
        await interaction.response.edit_message(embed=final_embed, view=None)
    @ui.button(label="一括承認", style=discord.ButtonStyle.success)
    async def approve_all_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.load_request_data()
        await self.finalize_approval(interaction, "approved", self.request_data["items"], [])
    @ui.button(label="一括却下", style=discord.ButtonStyle.danger)
    async def reject_all_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.load_request_data()
        await self.finalize_approval(interaction, "rejected", [], self.request_data["items"])
    @ui.button(label="個別審査", style=discord.ButtonStyle.secondary)
    async def partial_approval_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.load_request_data()
        view = PartialApprovalView(original_view=self)
        await interaction.response.edit_message(view=view)

# --- Bot Events ---
@bot.event
async def on_ready():
    print(f"✅ Botログイン成功: {bot.user}")
    data = load_data()
    for team_name, team_data in data.items():
        for user_id, user_requests in team_data.get("requests", {}).items():
            for req_id, req_data in user_requests.items():
                if req_data.get("status") == "pending":
                    bot.add_view(ApprovalView(request_id=req_id, team_name=team_name, applicant_id=req_data['applicant_id']))
    print(f"⚙️ {DATA_FILE} をロードし、未処理の申請Viewを登録しました。")

# --- Team Management Commands (変更なし) ---
@bot.command()
@commands.has_permissions(administrator=True)
async def setup_team(ctx, name: str, budget: int, role: discord.Role):
    data = load_data()
    channel_id = ctx.channel.id
    existing_team, _ = get_team_by_channel_id(channel_id, data)
    if existing_team:
        await ctx.send(f"⚠️ このチャンネルは既にチーム「{existing_team}」に紐づいています。")
        return
    if name in data:
        await ctx.send(f"⚠️ チーム名「{name}」は既に使用されています。")
        return
    data[name] = {
        "settings": {"name": name, "budget": budget, "role_id": role.id, "channel_id": channel_id},
        "requests": {}
    }
    save_data(data)
    await ctx.send(f"✅ チーム「{name}」を作成し、このチャンネルに紐付けました。\n"
                   f"- 初期予算: ¥{budget:,}\n"
                   f"- 承認ロール: {role.mention}")

@bot.command()
async def team_info(ctx):
    data = load_data()
    team_name, team_data = get_team_by_channel_id(ctx.channel.id, data)
    if not team_data:
        await ctx.send("このチャンネルはどのチームにも紐づいていません。")
        return
    settings = team_data.get("settings", {})
    budget = settings.get("budget", 0)
    role_id = settings.get("role_id")
    role = ctx.guild.get_role(role_id) if role_id else "未設定"
    embed = discord.Embed(title=f"💰 チーム「{team_name}」の情報", color=discord.Color.blue())
    embed.add_field(name="現在の予算残高", value=f"**¥{budget:,}**", inline=False)
    embed.add_field(name="承認ロール", value=role.mention if isinstance(role, discord.Role) else role, inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def update_budget(ctx, amount: int):
    data = load_data()
    team_name, team_data = get_team_by_channel_id(ctx.channel.id, data)
    if not team_data:
        await ctx.send("このチャンネルはどのチームにも紐づいていません。")
        return
    role_id = team_data.get("settings", {}).get("role_id")
    if not role_id or not any(r.id == role_id for r in ctx.author.roles):
        await ctx.send("⚠️ このコマンドを実行する権限がありません。")
        return
    team_data["settings"]["budget"] += amount
    save_data(data)
    await ctx.send(f"✅ チーム「{team_name}」の予算に ¥{amount:,} を追加しました。\n"
                   f"現在の残高: ¥{team_data['settings']['budget']:,}")

# --- Main Commands ---
@bot.command()
async def request(ctx):
    """このチームの予算で、購入申請を開始します。"""
    data = load_data()
    team_name, team_data = get_team_by_channel_id(ctx.channel.id, data)
    if not team_data:
        await ctx.send("⚠️ このチャンネルはチームに登録されていません。\n"
                       "管理者が `!setup_team <名前> <予算> <@ロール>` コマンドで設定してください。")
        return
    view = MultiItemRequestView(author=ctx.author, team_name=team_name, team_data=team_data)
    embed = view.create_embed()
    await ctx.send(embed=embed, view=view)

# ★★★ 変更点: このチーム内での申請履歴のみ表示するように修正 ★★★
@bot.command()
async def myrequests(ctx):
    """【このチーム内で】自分が過去に申請した内容の一覧を表示します。"""
    data = load_data()
    team_name, team_data = get_team_by_channel_id(ctx.channel.id, data)

    # チームに紐づいていないチャンネルでは実行不可
    if not team_data:
        await ctx.send("⚠️ このコマンドはチームに紐付けられたチャンネルでのみ使用できます。", ephemeral=True)
        return
    
    author_id_str = str(ctx.author.id)
    
    # このチーム内の自分の申請データを取得
    user_requests_data = team_data.get("requests", {}).get(author_id_str, {})
    
    if not user_requests_data:
        await ctx.send(f"チーム「{team_name}」内でのあなたの申請履歴はありません。", ephemeral=True)
        return
        
    # 辞書の値をリストに変換して新しい順にソート
    user_requests = sorted(user_requests_data.values(), key=lambda x: x['timestamp'], reverse=True)

    embed = discord.Embed(
        title=f"{ctx.author.display_name}さんの申請履歴",
        description=f"**チーム: {team_name}**",
        color=discord.Color.green()
    )
    
    description_body = ""
    for req in user_requests[:10]: # 表示件数を10件に制限
        status_emoji = {"pending": "⏳", "approved": "✅", "rejected": "❌"}.get(req['status'], '❔')
        timestamp = datetime.fromisoformat(req['timestamp']).strftime('%Y-%m-%d %H:%M')
        
        amount_str = f"¥{req['total_amount']:,}"
        if req['status'] == 'approved':
            amount_str = f"¥{req['approved_amount']:,} / {amount_str}"

        # 申請した品物の要約を追加
        items_summary = ", ".join([item['name'] for item in req['items']])
        if len(items_summary) > 50: # 長すぎる場合は省略
            items_summary = items_summary[:50] + "..."

        description_body += f"\n**申請:** {items_summary}\n"
        description_body += f"> {status_emoji} **ステータス:** {req['status']}\n"
        description_body += f"> **申請日時:** {timestamp}\n"
        description_body += f"> **金額(承認/申請):** {amount_str}\n"
        description_body += f"> **申請ID:** `{req['request_id']}`"
        description_body += "\n---"

    if len(user_requests) > 10:
        embed.set_footer(text=f"全{len(user_requests)}件中、最新10件を表示しています。")

    embed.description += description_body
    await ctx.send(embed=embed, ephemeral=True)


# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(TOKEN)