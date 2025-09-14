import discord
from discord import ui
from discord.ext import commands
import csv
import os
import json

# --- Bot and File Configuration ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- Load Configuration ---
with open("config.json", "r", encoding="utf-8") as config_file:
    config = json.load(config_file)

TOKEN = config["TOKEN"]
# ★ 変更点: config.jsonからの共通ロールID読み込みを維持
ALLOWED_ROLE_ID = config.get("ALLOWED_ROLE_ID")

### ▼▼▼ ファイル設定 ▼▼▼
DATA_DIR = "data"
REVIEW_LOG_FILE = "review_results.csv"
BUDGET_FILE = "budgets.json"
CHANNEL_FILE = "channels.json"
SETTINGS_FILE = "settings.json" # ロールIDなどを保存するファイル

# In-memory storage
# 形式: {guild_id: {"budgets": {...}, "channels": set(...), "additional_roles": set(...)}}
guild_data = {}

# --- Helper Functions for Server-Specific Data ---

def get_guild_data_path(guild_id: int) -> str:
    """サーバーIDに対応するデータディレクトリのパスを返し、なければ作成する"""
    path = os.path.join(DATA_DIR, str(guild_id))
    os.makedirs(path, exist_ok=True)
    return path

def load_guild_data(guild_id: int):
    """指定されたサーバーのデータをファイルから読み込み、メモリに格納する"""
    if guild_id in guild_data:
        return

    # メモリ上にサーバー用のデータ領域を初期化
    guild_data[guild_id] = {
        "budgets": {},
        "channels": set(),
        "additional_roles": set() # ★ 変更点: 複数の追加ロールを保持
    }
    
    guild_path = get_guild_data_path(guild_id)
    budget_file_path = os.path.join(guild_path, BUDGET_FILE)
    channel_file_path = os.path.join(guild_path, CHANNEL_FILE)
    settings_file_path = os.path.join(guild_path, SETTINGS_FILE)

    # 予算データの読み込み
    try:
        with open(budget_file_path, "r", encoding="utf-8") as f:
            guild_data[guild_id]["budgets"] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # チャンネルデータの読み込み
    try:
        with open(channel_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            guild_data[guild_id]["channels"] = set(data.get("registered_channels", []))
    except (FileNotFoundError, json.JSONDecodeError):
        pass
        
    # 設定データ（追加の会計ロールID）の読み込み
    try:
        with open(settings_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            # ★ 変更点: listで保存されたものをsetに変換
            guild_data[guild_id]["additional_roles"] = set(data.get("additional_role_ids", []))
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_budgets(guild_id: int):
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, BUDGET_FILE)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(guild_data[guild_id]["budgets"], f, indent=4, ensure_ascii=False)

def save_channels(guild_id: int):
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, CHANNEL_FILE)
    with open(file_path, "w", encoding="utf-8") as f:
        data_to_save = {"registered_channels": list(guild_data[guild_id]["channels"])}
        json.dump(data_to_save, f, indent=4)

def save_settings(guild_id: int):
    """サーバーの設定データ（追加ロール等）をファイルに保存する"""
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, SETTINGS_FILE)
    data_to_save = {
        # ★ 変更点: setは直接JSONにできないためlistに変換
        "additional_role_ids": list(guild_data[guild_id].get("additional_roles", []))
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, indent=4)

def save_review_result_partial(guild_id: int, applicant, budget_name, approver, approved_items, rejected_items):
    guild_path = get_guild_data_path(guild_id)
    log_file_path = os.path.join(guild_path, REVIEW_LOG_FILE)
    file_exists = os.path.exists(log_file_path)
    with open(log_file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["申請者", "購入物", "リンク", "金額", "結果", "承認者", "予算項目"])
        for item in approved_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "承認", approver, budget_name])
        for item in rejected_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "却下", approver, budget_name])

# --- Helper Function for Permission Check ---

def has_accounting_role(member: discord.Member) -> bool:
    """メンバーが会計関連のロールを持っているかチェックする"""
    if not member.guild:
        return False
        
    load_guild_data(member.guild.id)
    server_roles = guild_data[member.guild.id].get("additional_roles", set())
    
    member_role_ids = {role.id for role in member.roles}
    
    # 共通ロールを持っているかチェック
    if ALLOWED_ROLE_ID and ALLOWED_ROLE_ID in member_role_ids:
        return True
    
    # サーバー固有の追加ロールを持っているかチェック
    if not server_roles.isdisjoint(member_role_ids): # 積集合があればTrue
        return True
        
    return False

# --- UI Classes (AddItemModal, MultiItemRequestView, PartialApprovalViewは変更なし) ---
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

        new_item = {
            "name": self.item_name.value,
            "link": self.link.value,
            "amount": amount_val,
            "status": "保留"
        }
        self.parent_view.items.append(new_item)
        await self.parent_view.update_message(interaction)

class MultiItemRequestView(ui.View):
    def __init__(self, author: discord.User, guild_id: int):
        super().__init__(timeout=600)
        self.author = author
        self.guild_id = guild_id
        self.items = []
        self.selected_budget = None
        self.update_budget_options()
    
    def update_budget_options(self):
        load_guild_data(self.guild_id)
        current_budgets = guild_data[self.guild_id]["budgets"]
        
        options = [
            discord.SelectOption(label=name, description=f"残高: ¥{amount:,}")
            for name, amount in current_budgets.items()
        ]
        if not options:
            self.children[1].disabled = True
            self.children[1].placeholder = "利用可能な予算がありません"
        else:
            self.children[1].options = options

    def create_embed(self):
        embed = discord.Embed(title="🛒 購入申請リスト", color=discord.Color.blue())
        embed.set_author(name=f"申請者: {self.author.display_name}", icon_url=self.author.display_avatar)
        
        total_amount = 0
        if not self.items:
            embed.description = "まだ品物は追加されていません。\n「品物を追加」ボタンから入力してください。"
        else:
            description = ""
            for i, item in enumerate(self.items, 1):
                description += f"**{i}. {item['name']}** - ¥{item['amount']:,}\n"
                if item['link']:
                    description += f"   [リンク]({item['link']})\n"
                total_amount += item['amount']
            embed.description = description
            embed.add_field(name="合計金額", value=f"**¥{total_amount:,}**")

        if self.selected_budget:
            embed.add_field(name="選択中の予算", value=self.selected_budget)
        return embed

    async def update_message(self, interaction: discord.Interaction):
        submit_button = self.children[2]
        submit_button.disabled = not (self.items and self.selected_budget)
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

    @ui.select(placeholder="① 使用する予算を選択してください...")
    async def select_budget(self, interaction: discord.Interaction, select: ui.Select):
        self.selected_budget = select.values[0]
        await self.update_message(interaction)
    
    @ui.button(label="申請を提出", style=discord.ButtonStyle.primary, emoji="🚀", disabled=True)
    async def submit_button(self, interaction: discord.Interaction, button: ui.Button):
        total_amount = sum(item['amount'] for item in self.items)
        current_budgets = guild_data[self.guild_id]["budgets"]
        
        ### ▼ 変更点 ▼ ###
        # 予算不足でも申請できるように、以下のチェックをコメントアウト
        # if total_amount > current_budgets.get(self.selected_budget, 0):
        #     await interaction.response.send_message(f"⚠️ **エラー:** 予算「{self.selected_budget}」が不足しています。(残高: ¥{current_budgets.get(self.selected_budget, 0):,})", ephemeral=True)
        #     return
        
        final_embed = discord.Embed(title="購入申請", color=discord.Color.gold())
        final_embed.set_author(name=f"申請者: {self.author.display_name}", icon_url=self.author.display_avatar)
        description = ""
        for i, item in enumerate(self.items, 1):
            description += f"**{i}. {item['name']}** - ¥{item['amount']:,}\n"
            if item['link']:
                description += f"   [リンク]({item['link']})\n"
        final_embed.description = description
        final_embed.add_field(name="💰 合計金額", value=f"**¥{total_amount:,}**", inline=True)
        final_embed.add_field(name="🧾 予算項目", value=self.selected_budget, inline=True)
        final_embed.set_footer(text="ステータス: 審査中")
        await interaction.message.delete()
        approval_view = ApprovalView(author=self.author, items=self.items, budget_name=self.selected_budget, guild_id=self.guild_id)
        await interaction.channel.send(embed=final_embed, view=approval_view)

class PartialApprovalView(ui.View):
    def __init__(self, original_view: 'ApprovalView'):
        super().__init__(timeout=300)
        self.original_view = original_view
        self.items = original_view.items
        
        options = [
            discord.SelectOption(label=f"{item['name']} (¥{item['amount']:,})", value=str(i))
            for i, item in enumerate(self.items)
        ]
        
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
        approved_items = []
        rejected_items = []

        for i, item in enumerate(self.items):
            if i in approved_indices:
                approved_items.append(item)
            else:
                rejected_items.append(item)
        await self.original_view.finalize_approval(interaction, approved_items, rejected_items)

class ApprovalView(ui.View):
    def __init__(self, author: discord.User, items: list, budget_name: str, guild_id: int):
        super().__init__(timeout=None)
        self.author = author
        self.items = items
        self.budget_name = budget_name
        self.guild_id = guild_id
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # ★ 変更点: 共通の権限チェック関数を呼び出す
        if not has_accounting_role(interaction.user):
            await interaction.response.send_message("⚠️ この申請を審査する権限がありません。", ephemeral=True)
            return False
        return True

    async def finalize_approval(self, interaction: discord.Interaction, approved_items: list, rejected_items: list):
        approver = interaction.user
        approved_amount = sum(item['amount'] for item in approved_items)
        load_guild_data(self.guild_id)
        current_budgets = guild_data[self.guild_id]["budgets"]

        #if approved_amount > current_budgets.get(self.budget_name, 0):
            #await interaction.response.send_message(f"⚠️ **エラー:** 承認額 (¥{approved_amount:,}) が予算「{self.budget_name}」の残高を超えています。", ephemeral=True)
            #return

        if approved_amount > 0:
            current_budgets[self.budget_name] -= approved_amount
            save_budgets(self.guild_id)
        
        final_embed = discord.Embed(title="審査結果", color=discord.Color.dark_grey())
        final_embed.set_author(name=f"申請者: {self.author.display_name}", icon_url=self.author.display_avatar)

        description = ""
        if approved_items:
            description += "**✅ 承認された品物**\n"
            for item in approved_items:
                description += f"- {item['name']} (¥{item['amount']:,}) {item['link']}\n"
        if rejected_items:
            description += "\n**❌ 却下された品物**\n"
            for item in rejected_items:
                description += f"- {item['name']} (¥{item['amount']:,}) {item['link']}\n"
        final_embed.description = description

        footer_text = f"審査者: {approver.display_name}"
        if approved_amount > 0:
            footer_text += f"\n「{self.budget_name}」から ¥{approved_amount:,} を支出 (残高: ¥{current_budgets[self.budget_name]:,})"
        final_embed.set_footer(text=footer_text)

        save_review_result_partial(self.guild_id, self.author.display_name, self.budget_name, approver.display_name, approved_items, rejected_items)
        await interaction.response.edit_message(embed=final_embed, view=None)

    @ui.button(label="一括承認", style=discord.ButtonStyle.success)
    async def approve_all_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.finalize_approval(interaction, self.items, [])

    @ui.button(label="一括却下", style=discord.ButtonStyle.danger)
    async def reject_all_button(self, interaction: discord.Interaction, button: ui.Button):
        await self.finalize_approval(interaction, [], self.items)

    @ui.button(label="個別審査", style=discord.ButtonStyle.secondary)
    async def partial_approval_button(self, interaction: discord.Interaction, button: ui.Button):
        view = PartialApprovalView(original_view=self)
        await interaction.response.edit_message(view=view)

# --- Bot Checks and Events ---

@bot.check
async def is_in_registered_channel(ctx: commands.Context):
    if not ctx.guild:
        return False
    load_guild_data(ctx.guild.id)
    registered_channels = guild_data[ctx.guild.id]["channels"]
    # ★ 変更点: 新しいロール管理コマンドをチェック対象に追加
    management_commands = [
        "register_channel", "unregister_channel", "list_channels", 
        "add_accounting_role", "remove_accounting_role", "list_accounting_roles"
    ]
    if ctx.command and ctx.command.name in management_commands:
        return True
    if not registered_channels:
        return True
    return ctx.channel.id in registered_channels
    
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CheckFailure):
        return
    if isinstance(error, commands.MissingPermissions):
        await ctx.send("⚠️ このコマンドを実行する管理者権限がありません。")
    elif isinstance(error, commands.CommandNotFound):
        pass
    else:
        print(f"Unhandled error: {error}")

@bot.event
async def on_ready():
    print(f"✅ Botログイン成功: {bot.user}")
    os.makedirs(DATA_DIR, exist_ok=True)
    print("--- 所属サーバーのデータチェック ---")
    for guild in bot.guilds:
        print(f"-> サーバー: {guild.name} ({guild.id})")
        load_guild_data(guild.id)
    print("---------------------------------")
    print("📢 全サーバーのデータをロードしました。")

# --- Management Commands (Roles & Channels) ---

# ★★★ 新しい会計ロール管理コマンド ★★★
@bot.command()
@commands.has_permissions(administrator=True)
async def add_accounting_role(ctx, role: discord.Role):
    """【管理者用】このサーバーで会計権限を持つロールを追加します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    
    if role.id in guild_data[guild_id]["additional_roles"]:
        await ctx.send(f"✅ ロール {role.mention} は既に登録されています。")
        return
        
    guild_data[guild_id]["additional_roles"].add(role.id)
    save_settings(guild_id)
    await ctx.send(f"✅ 会計ロールとして {role.mention} を追加しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def remove_accounting_role(ctx, role: discord.Role):
    """【管理者用】サーバーに追加された会計ロールを削除します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)

    if role.id not in guild_data[guild_id]["additional_roles"]:
        await ctx.send(f"ℹ️ ロール {role.mention} はこのサーバーの会計ロールとして登録されていません。")
        return

    guild_data[guild_id]["additional_roles"].discard(role.id)
    save_settings(guild_id)
    await ctx.send(f"✅ 会計ロール {role.mention} を削除しました。")

@bot.command()
async def list_accounting_roles(ctx):
    """現在有効な会計ロールの一覧を表示します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    
    embed = discord.Embed(title="🔒 会計権限を持つロール一覧", color=discord.Color.dark_green())
    
    # 共通ロール
    common_role_str = "未設定"
    if ALLOWED_ROLE_ID:
        role = ctx.guild.get_role(ALLOWED_ROLE_ID)
        common_role_str = role.mention if role else f"不明なロール (ID: {ALLOWED_ROLE_ID})"
    embed.add_field(name="共通ロール (config.jsonで指定)", value=common_role_str, inline=False)
    
    # サーバー追加ロール
    server_roles = guild_data[guild_id].get("additional_roles", set())
    server_roles_str = "なし"
    if server_roles:
        role_mentions = []
        for role_id in server_roles:
            role = ctx.guild.get_role(role_id)
            role_mentions.append(role.mention if role else f"不明なロール (ID: {role_id})")
        server_roles_str = "\n".join(role_mentions)
    embed.add_field(name="このサーバーで追加されたロール", value=server_roles_str, inline=False)
    
    await ctx.send(embed=embed)


@bot.command()
@commands.has_permissions(administrator=True)
async def register_channel(ctx, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    if target_channel.id in guild_data[guild_id]["channels"]:
        await ctx.send(f"✅ チャンネル {target_channel.mention} は既に登録されています。")
        return
    guild_data[guild_id]["channels"].add(target_channel.id)
    save_channels(guild_id)
    await ctx.send(f"✅ チャンネル {target_channel.mention} を登録しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def unregister_channel(ctx, channel: discord.TextChannel = None):
    target_channel = channel or ctx.channel
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    if target_channel.id not in guild_data[guild_id]["channels"]:
        await ctx.send(f"ℹ️ チャンネル {target_channel.mention} は登録されていません。")
        return
    guild_data[guild_id]["channels"].discard(target_channel.id)
    save_channels(guild_id)
    await ctx.send(f"✅ チャンネル {target_channel.mention} の登録を解除しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def list_channels(ctx):
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    registered_channels = guild_data[guild_id]["channels"]
    if not registered_channels:
        await ctx.send("現在、このサーバーで登録されているチャンネルはありません。")
        return
    embed = discord.Embed(title="📢 登録済みチャンネル一覧", color=discord.Color.blue())
    channel_links = [f"<#{channel_id}>" for channel_id in registered_channels]
    embed.description = "\n".join(channel_links)
    await ctx.send(embed=embed)


# --- Bot Commands ---

@bot.command()
async def request(ctx):
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    if not guild_data[guild_id]["budgets"]:
        await ctx.send("現在、このサーバーで登録されている予算はありません。")
        return
    view = MultiItemRequestView(author=ctx.author, guild_id=guild_id)
    embed = view.create_embed()
    await ctx.send(embed=embed, view=view)
    
@bot.command()
async def budget(ctx):
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    current_budgets = guild_data[guild_id]["budgets"]
    if not current_budgets:
        await ctx.send("現在、このサーバーで登録されている予算はありません。")
        return
    embed = discord.Embed(title="💰 現在の予算状況", color=discord.Color.gold())
    for name, amount in current_budgets.items():
        embed.add_field(name=name, value=f"¥{amount:,}", inline=False)
    await ctx.send(embed=embed)

@bot.command()
async def add_budget(ctx, name: str, amount: int):
    """【会計ロール用】予算を追加・補充します。"""
    # ★ 変更点: 共通の権限チェック関数を呼び出す
    if not has_accounting_role(ctx.author):
        await ctx.send("⚠️ このコマンドを実行する権限がありません。")
        return
        
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    current_budgets = guild_data[guild_id]["budgets"]
    current_budgets[name] = current_budgets.get(name, 0) + amount
    save_budgets(guild_id)
    await ctx.send(f"✅ 予算「{name}」に ¥{amount:,} を追加しました。現在の残高: ¥{current_budgets[name]:,}")
    
@bot.command()
async def send(ctx, applicant: str, item: str, link: str, amount: int, budget_name: str):
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    current_budgets = guild_data[guild_id]["budgets"]
    
    if budget_name not in current_budgets:
        await ctx.send(f"⚠️ **エラー:** 予算項目「{budget_name}」は存在しません。")
        return
     
    ### ▼ 変更点 ▼ ###
    # 予算不足でも申請できるように、以下のチェックをコメントアウト
    #if amount > current_budgets[budget_name]:
        #await ctx.send(f"⚠️ **エラー:** 予算「{budget_name}」が不足しています。(残高: ¥{current_budgets[budget_name]:,})")
        #return

    embed = discord.Embed(title="購入申請", color=discord.Color.gold())
    embed.set_author(name=f"申請者: {applicant}", icon_url=ctx.author.display_avatar)
    embed.add_field(name="購入物", value=item, inline=False)
    embed.add_field(name="リンク", value=link, inline=False)
    embed.add_field(name="💰 金額", value=f"¥{amount:,}", inline=True)
    embed.add_field(name="🧾 予算項目", value=budget_name, inline=True)
    embed.set_footer(text="ステータス: 審査中")

    items = [{"name": item, "link": link, "amount": amount}]
    approval_view = ApprovalView(author=ctx.author, items=items, budget_name=budget_name, guild_id=guild_id)
    await ctx.send(embed=embed, view=approval_view)

@bot.command()
async def export_csv(ctx):
    """【会計ロール用】これまでの申請・審査結果をCSVファイルで出力します。"""
    # コマンド実行者が会計ロールを持っているかチェック
    if not has_accounting_role(ctx.author):
        await ctx.send("⚠️ このコマンドを実行する権限がありません。")
        return

    guild_id = ctx.guild.id
    # サーバー固有のデータパスを取得
    guild_path = get_guild_data_path(guild_id)
    log_file_path = os.path.join(guild_path, REVIEW_LOG_FILE)

    # ログファイルの存在を確認
    if not os.path.exists(log_file_path):
        await ctx.send("ℹ️ まだ審査記録がありません。CSVファイルは作成されていません。")
        return

    try:
        # ファイルをDiscordのチャットに送信
        await ctx.send(
            content=f"📄 {ctx.guild.name} の申請・審査記録です。",
            file=discord.File(log_file_path, "review_results.csv")
        )
    except Exception as e:
        await ctx.send(f"⚠️ ファイルの送信中にエラーが発生しました: {e}")
        print(f"Error sending CSV file for guild {guild_id}: {e}")

# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(TOKEN)