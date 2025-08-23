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
# ALLOWED_ROLE_ID = config["ALLOWED_ROLE_ID"] # サーバーごとの設定に移行するため不要に

### ▼▼▼ 変更点 ▼▼▼
# ファイル名を定数化し、データディレクトリと設定ファイル名を設定
DATA_DIR = "data"
REVIEW_LOG_FILE = "review_results.csv"
BUDGET_FILE = "budgets.json"
CHANNEL_FILE = "channels.json"
SETTINGS_FILE = "settings.json" # ★ 追加：ロールIDなどを保存するファイル

# In-memory storage
# サーバーごとのデータを保持する辞書
# 形式: {guild_id: {"budgets": {...}, "channels": set(...), "accounting_role_id": int}}
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
        return # 既にロード済みなら何もしない

    # メモリ上にサーバー用のデータ領域を初期化
    guild_data[guild_id] = {
        "budgets": {},
        "channels": set(),
        "accounting_role_id": None # ★ 追加: 会計ロールIDの格納場所
    }
    
    guild_path = get_guild_data_path(guild_id)
    budget_file_path = os.path.join(guild_path, BUDGET_FILE)
    channel_file_path = os.path.join(guild_path, CHANNEL_FILE)
    settings_file_path = os.path.join(guild_path, SETTINGS_FILE) # ★ 追加

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
        
    # ★ 追加: 設定データ（会計ロールID）の読み込み
    try:
        with open(settings_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            guild_data[guild_id]["accounting_role_id"] = data.get("accounting_role_id")
    except (FileNotFoundError, json.JSONDecodeError):
        pass

def save_budgets(guild_id: int):
    """指定されたサーバーの予算データをファイルに保存する"""
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, BUDGET_FILE)
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(guild_data[guild_id]["budgets"], f, indent=4, ensure_ascii=False)

def save_channels(guild_id: int):
    """指定されたサーバーのチャンネル登録情報をファイルに保存する"""
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, CHANNEL_FILE)
    with open(file_path, "w", encoding="utf-8") as f:
        data_to_save = {"registered_channels": list(guild_data[guild_id]["channels"])}
        json.dump(data_to_save, f, indent=4)

# ★ 追加: 設定データ（会計ロールID）を保存する関数
def save_settings(guild_id: int):
    """指定されたサーバーの設定データ（会計ロール等）をファイルに保存する"""
    path = get_guild_data_path(guild_id)
    file_path = os.path.join(path, SETTINGS_FILE)
    data_to_save = {
        "accounting_role_id": guild_data[guild_id].get("accounting_role_id")
    }
    with open(file_path, "w", encoding="utf-8") as f:
        json.dump(data_to_save, f, indent=4)

def save_review_result_partial(guild_id: int, applicant, budget_name, approver, approved_items, rejected_items):
    """個別審査の結果をサーバーごとのCSVに記録する"""
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

# --- UI Classes (AddItemModal, MultiItemRequestView, PartialApprovalView) ---
# (UIクラスの前半は変更なしのため省略します)
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
        
        if total_amount > current_budgets.get(self.selected_budget, 0):
            await interaction.response.send_message(f"⚠️ **エラー:** 予算「{self.selected_budget}」が不足しています。(残高: ¥{current_budgets.get(self.selected_budget, 0):,})", ephemeral=True)
            return
        
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
        self.total_amount = sum(item['amount'] for item in items)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # ★ 変更点: サーバーごとに設定されたロールIDで権限をチェック
        member = interaction.user
        load_guild_data(self.guild_id)
        accounting_role_id = guild_data[self.guild_id].get("accounting_role_id")

        if not accounting_role_id:
            await interaction.response.send_message("⚠️ 審査用の会計ロールが設定されていません。管理者に設定を依頼してください。", ephemeral=True)
            return False

        has_role = any(role.id == accounting_role_id for role in member.roles)
        if not has_role:
            await interaction.response.send_message("⚠️ この申請を審査する権限がありません。", ephemeral=True)
            return False
        return True

    async def finalize_approval(self, interaction: discord.Interaction, approved_items: list, rejected_items: list):
        approver = interaction.user
        approved_amount = sum(item['amount'] for item in approved_items)

        load_guild_data(self.guild_id)
        current_budgets = guild_data[self.guild_id]["budgets"]

        if approved_amount > current_budgets.get(self.budget_name, 0):
            await interaction.response.send_message(f"⚠️ **エラー:** 承認額 (¥{approved_amount:,}) が予算「{self.budget_name}」の残高を超えています。", ephemeral=True)
            return

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

    management_commands = ["register_channel", "unregister_channel", "list_channels", "set_accounting_role", "get_accounting_role"]
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
        await ctx.send("⚠️ このコマンドを実行する管理者権限がありません。", ephemeral=True)
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

# --- Management Commands (Channels & Roles) ---

# ★★★ 新しい会計ロール管理コマンド ★★★
@bot.command()
@commands.has_permissions(administrator=True)
async def set_accounting_role(ctx, role: discord.Role):
    """【管理者用】予算の追加や申請の承認ができる会計ロールを設定します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    
    guild_data[guild_id]["accounting_role_id"] = role.id
    save_settings(guild_id)
    
    await ctx.send(f"✅ 会計ロールを「{role.mention}」に設定しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def get_accounting_role(ctx):
    """【管理者用】現在設定されている会計ロールを表示します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    
    role_id = guild_data[guild_id].get("accounting_role_id")
    if role_id:
        role = ctx.guild.get_role(role_id)
        if role:
            await ctx.send(f"現在の会計ロールは {role.mention} です。")
        else:
            await ctx.send(f"ℹ️ 会計ロールは設定されていますが、ロールが見つかりませんでした (ID: {role_id})。削除された可能性があります。")
    else:
        await ctx.send("現在、会計ロールは設定されていません。")

@bot.command()
@commands.has_permissions(administrator=True)
async def register_channel(ctx, channel: discord.TextChannel = None):
    """【管理者用】Botが反応するチャンネルを登録します。"""
    target_channel = channel or ctx.channel
    guild_id = ctx.guild.id
    load_guild_data(guild_id)

    if target_channel.id in guild_data[guild_id]["channels"]:
        await ctx.send(f"✅ チャンネル {target_channel.mention} は既に登録されています。", ephemeral=True)
        return

    guild_data[guild_id]["channels"].add(target_channel.id)
    save_channels(guild_id)
    await ctx.send(f"✅ チャンネル {target_channel.mention} を登録しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def unregister_channel(ctx, channel: discord.TextChannel = None):
    """【管理者用】チャンネルの登録を解除します。"""
    target_channel = channel or ctx.channel
    guild_id = ctx.guild.id
    load_guild_data(guild_id)

    if target_channel.id not in guild_data[guild_id]["channels"]:
        await ctx.send(f"ℹ️ チャンネル {target_channel.mention} は登録されていません。", ephemeral=True)
        return

    guild_data[guild_id]["channels"].discard(target_channel.id)
    save_channels(guild_id)
    await ctx.send(f"✅ チャンネル {target_channel.mention} の登録を解除しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def list_channels(ctx):
    """【管理者用】登録されているチャンネルの一覧を表示します。"""
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
    """購入申請のUIを呼び出します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    if not guild_data[guild_id]["budgets"]:
        await ctx.send("現在、このサーバーで登録されている予算はありません。管理者に予算の追加を依頼してください。")
        return
    view = MultiItemRequestView(author=ctx.author, guild_id=guild_id)
    embed = view.create_embed()
    await ctx.send(embed=embed, view=view)
    
@bot.command()
async def budget(ctx):
    """現在の全予算の状況を表示します。"""
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

# ★ 変更点: @commands.has_role() を削除し、コマンド内で手動チェック
@bot.command()
async def add_budget(ctx, name: str, amount: int):
    """【会計ロール用】予算を追加・補充します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)

    # --- 手動でロールチェック ---
    accounting_role_id = guild_data[guild_id].get("accounting_role_id")
    if not accounting_role_id:
        await ctx.send("⚠️ 会計ロールが設定されていません。管理者が `!set_accounting_role` で設定してください。")
        return
    
    member = ctx.author
    has_role = any(role.id == accounting_role_id for role in member.roles)
    if not has_role:
        await ctx.send("⚠️ このコマンドを実行する権限がありません。")
        return
    # --- ここまで ---

    current_budgets = guild_data[guild_id]["budgets"]
    current_budgets[name] = current_budgets.get(name, 0) + amount
    save_budgets(guild_id)
    await ctx.send(f"✅ 予算「{name}」に ¥{amount:,} を追加しました。現在の残高: ¥{current_budgets[name]:,}")
    
# add_budget_error は手動チェックになったため不要
    
@bot.command()
async def send(ctx, applicant: str, item: str, link: str, amount: int, budget_name: str):
    """【引数指定用】購入申請を作成します。"""
    guild_id = ctx.guild.id
    load_guild_data(guild_id)
    current_budgets = guild_data[guild_id]["budgets"]
    
    if budget_name not in current_budgets:
        await ctx.send(f"⚠️ **エラー:** 予算項目「{budget_name}」は存在しません。")
        return
        
    if amount > current_budgets[budget_name]:
        await ctx.send(f"⚠️ **エラー:** 予算「{budget_name}」が不足しています。(残高: ¥{current_budgets[budget_name]:,})")
        return

    embed = discord.Embed(title="購入申請", color=discord.Color.gold())
    # 申請者名を指定できるようにしつつ、アイコンはコマンド実行者にする
    embed.set_author(name=f"申請者: {applicant}", icon_url=ctx.author.display_avatar)
    embed.add_field(name="購入物", value=item, inline=False)
    embed.add_field(name="リンク", value=link, inline=False)
    embed.add_field(name="💰 金額", value=f"¥{amount:,}", inline=True)
    embed.add_field(name="🧾 予算項目", value=budget_name, inline=True)
    embed.set_footer(text="ステータス: 審査中")

    # authorをUserオブジェクトとして渡す必要があるため、ctx.authorを使用
    # 実際の申請者名はEmbedに表示されているので、これで問題ない
    items = [{"name": item, "link": link, "amount": amount}]
    approval_view = ApprovalView(
        author=ctx.author,
        items=items,
        budget_name=budget_name,
        guild_id=guild_id
    )
    await ctx.send(embed=embed, view=approval_view)

# --- Run the Bot ---
if __name__ == "__main__":
    bot.run(TOKEN)