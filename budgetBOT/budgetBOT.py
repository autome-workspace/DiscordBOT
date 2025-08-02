import discord
from discord import ui
from discord.ext import commands
import csv
import os
import json

# (設定やヘルパー関数、AddItemModal、MultiItemRequestViewの前半は変更なしのため省略します)
# --- Bot and File Configuration ---
intents = discord.Intents.default()
intents.messages = True
intents.message_content = True
intents.reactions = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents)

# --- User-defined Settings ---
TOKEN = ""
ALLOWED_ROLE_ID = 1400428622673875005#1394825200159297566
REVIEW_LOG_FILE = "review_results.csv"
BUDGET_FILE = "budgets.json"
CHANNEL_FILE = "channels.json" # ★ 追加: チャンネル登録ファイル



# --- Load Configuration ---
with open("config.json", "r", encoding="utf-8") as config_file:
    config = json.load(config_file)

TOKEN = config["TOKEN"]
ALLOWED_ROLE_ID = config["ALLOWED_ROLE_ID"]

### ▼▼▼ 変更点 ▼▼▼
# ファイル名を定数化し、データディレクトリを設定
DATA_DIR = "data"
REVIEW_LOG_FILE = "review_results.csv"
BUDGET_FILE = "budgets.json"
CHANNEL_FILE = "channels.json"


# In-memory storage
# サーバーごとのデータを保持する辞書
# 形式: {guild_id: {"budgets": {...}, "channels": set(...)}}
guild_data = {}
message_data_map = {}
budgets = {}
registered_channel_ids = set() # ★ 追加: 登録チャンネルIDを保持するセット

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
        "channels": set()
    }
    
    guild_path = get_guild_data_path(guild_id)
    budget_file_path = os.path.join(guild_path, BUDGET_FILE)
    channel_file_path = os.path.join(guild_path, CHANNEL_FILE)

    # 予算データの読み込み
    try:
        with open(budget_file_path, "r", encoding="utf-8") as f:
            guild_data[guild_id]["budgets"] = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass # ファイルがなければ空のまま

    # チャンネルデータの読み込み
    try:
        with open(channel_file_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            guild_data[guild_id]["channels"] = set(data.get("registered_channels", []))
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
        # setは直接JSON化できないためlistに変換
        data_to_save = {"registered_channels": list(guild_data[guild_id]["channels"])}
        json.dump(data_to_save, f, indent=4)

def save_review_result_partial(guild_id: int, applicant, budget_name, approver, approved_items, rejected_items):
    """個別審査の結果をサーバーごとのCSVに記録する"""
    guild_path = get_guild_data_path(guild_id)
    log_file_path = os.path.join(guild_path, REVIEW_LOG_FILE)
    
    # ファイルがなければヘッダーを書き込む
    file_exists = os.path.exists(log_file_path)
    
    with open(log_file_path, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow(["申請者", "購入物", "リンク", "金額", "結果", "承認者", "予算項目"])
        
        for item in approved_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "承認", approver, budget_name])
        for item in rejected_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "却下", approver, budget_name])

# --- UI Classes for Multi-Item Request ---
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
            "status": "保留" #審査中のステータス
        }
        self.parent_view.items.append(new_item)
        await self.parent_view.update_message(interaction)

# (MultiItemRequestViewは承認UIを呼び出すように変更)
class MultiItemRequestView(ui.View):
    def __init__(self, author: discord.User, guild_id: int):
        super().__init__(timeout=600)
        self.author = author
        self.guild_id = guild_id # サーバーIDを保持
        self.items = []
        self.selected_budget = None
        self.update_budget_options()
    
    def update_budget_options(self):
        # サーバー固有のデータを読み込む
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
        # ... (変更なし)
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
        # ... (変更なし)
        submit_button = self.children[2]
        submit_button.disabled = not (self.items and self.selected_budget)
        
        embed = self.create_embed()
        await interaction.response.edit_message(embed=embed, view=self)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # ... (変更なし)
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("⚠️ この申請を操作できるのは申請者本人のみです。", ephemeral=True)
            return False
        return True

    @ui.button(label="品物を追加", style=discord.ButtonStyle.secondary, emoji="➕")
    async def add_item_button(self, interaction: discord.Interaction, button: ui.Button):
        # ... (変更なし)
        modal = AddItemModal(parent_view=self)
        await interaction.response.send_modal(modal)

    @ui.select(placeholder="① 使用する予算を選択してください...")
    async def select_budget(self, interaction: discord.Interaction, select: ui.Select):
        # ... (変更なし)
        self.selected_budget = select.values[0]
        await self.update_message(interaction)
    
    @ui.button(label="申請を提出", style=discord.ButtonStyle.primary, emoji="🚀", disabled=True)
    async def submit_button(self, interaction: discord.Interaction, button: ui.Button):
        # ★ 変更点: 最終申請メッセージにリアクションの代わりに承認Viewを付ける
        total_amount = sum(item['amount'] for item in self.items)

        if total_amount > budgets.get(self.selected_budget, 0):
            await interaction.response.send_message(f"⚠️ **エラー:** 予算「{self.selected_budget}」が不足しています。(残高: ¥{budgets[self.selected_budget]:,})", ephemeral=True)
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
        
        # 承認UIを作成してメッセージを送信
        # 承認UIに guild_id を渡す
        approval_view = ApprovalView(
            author=self.author,
            items=self.items,
            budget_name=self.selected_budget,
            guild_id=self.guild_id # guild_id を渡す
        )
        approval_message = await interaction.channel.send(embed=final_embed, view=approval_view)

# ★★★ 新しいUIクラス ★★★

# 個別審査用のUI
# ★★★ 修正箇所 ★★★

# 個別審査用のUI
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
        # ★ 修正点: プルダウン操作のインタラクションを「保留」して応答する
        # これにより、インタラクション失敗のエラーを防ぐ
        await interaction.response.defer()

    @ui.button(label="この内容で確定", style=discord.ButtonStyle.success)
    async def confirm_button(self, interaction: discord.Interaction, button: ui.Button):
        # 承認する品物が1つも選択されていない場合は、何もしない
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
        
        # finalize_approvalを呼び出すのは変更なし
        await self.original_view.finalize_approval(interaction, approved_items, rejected_items)

# --- UI Classes for Approval ---

# ★★★ 修正箇所 ★★★
# 承認者が操作する最初のUI
class ApprovalView(ui.View):
    def __init__(self, author: discord.User, items: list, budget_name: str, guild_id: int):
        super().__init__(timeout=None) # 永続化する場合は bot.add_view() での工夫が必要
        self.author = author
        self.items = items
        self.budget_name = budget_name
        self.guild_id = guild_id # サーバーIDを保持
        self.total_amount = sum(item['amount'] for item in items)
    
    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        # 特定のロールを持つ人だけが操作可能
        member = interaction.user
        has_role = any(role.id == ALLOWED_ROLE_ID for role in member.roles)
        if not has_role:
            await interaction.response.send_message("⚠️ この申請を審査する権限がありません。", ephemeral=True)
            return False
        return True

    async def finalize_approval(self, interaction: discord.Interaction, approved_items: list, rejected_items: list):
        """承認・却下処理を確定させる共通関数"""
        approver = interaction.user
        approved_amount = sum(item['amount'] for item in approved_items)

         # サーバー固有のデータを操作
        load_guild_data(self.guild_id)
        current_budgets = guild_data[self.guild_id]["budgets"]

        if approved_amount > current_budgets.get(self.budget_name, 0):
            await interaction.response.send_message(f"⚠️ **エラー:** 承認額 (¥{approved_amount:,}) が予算「{self.budget_name}」の残高を超えています。", ephemeral=True)
            return

        # 予算引き落とし
        if approved_amount > 0:
            current_budgets[self.budget_name] -= approved_amount
            save_budgets(self.guild_id) # guild_id を指定して保存
        
        # 最終結果のEmbedを作成
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
            footer_text += f"\n「{self.budget_name}」から ¥{approved_amount:,} を支出 (残高: ¥{budgets[self.budget_name]:,})"
        final_embed.set_footer(text=footer_text)

        # ログを保存
        # ログを保存する際も guild_id を渡す
        save_review_result_partial(self.guild_id, self.author.display_name, self.budget_name, approver.display_name, approved_items, rejected_items)
        
        # 元のメッセージを更新し、UIを無効化
        await interaction.response.edit_message(embed=final_embed, view=None)

    @ui.button(label="一括承認", style=discord.ButtonStyle.success)
    async def approve_all_button(self, interaction: discord.Interaction, button: ui.Button):
        # 正しい処理: 個別審査のUIは表示せず、直接ファイナライズ関数を呼ぶ
        # self.items（全品物）を承認済みリストとして渡す
        await self.finalize_approval(interaction, self.items, [])

    @ui.button(label="一括却下", style=discord.ButtonStyle.danger)
    async def reject_all_button(self, interaction: discord.Interaction, button: ui.Button):
        # 正しい処理: 承認済みリストを空にしてファイナライズ関数を呼ぶ
        await self.finalize_approval(interaction, [], self.items)

    @ui.button(label="個別審査", style=discord.ButtonStyle.secondary)
    async def partial_approval_button(self, interaction: discord.Interaction, button: ui.Button):
        # 正しい処理: 個別審査用のUI (PartialApprovalView) を表示する
        view = PartialApprovalView(original_view=self)
        await interaction.response.edit_message(view=view)


# ★★★ Bot全体に適用するチャンネルチェック（デバッグ版） ★★★
@bot.check
async def is_in_registered_channel(ctx: commands.Context):
    """コマンドが、そのサーバーで登録されたチャンネルでのみ実行されるようにチェックする"""
    if not ctx.guild:
        return False # DMなど、サーバー以外からのコマンドは弾く

    # データを読み込む
    load_guild_data(ctx.guild.id)
    registered_channels = guild_data[ctx.guild.id]["channels"]

    # 管理用コマンドはどこでもOK
    management_commands = ["register_channel", "unregister_channel", "list_channels"]
    if ctx.command and ctx.command.name in management_commands:
        return True
    
    # 登録チャンネルがなければどこでもOK (初期設定用)
    if not registered_channels:
        return True
    
    return ctx.channel.id in registered_channels
@bot.event
async def on_command_error(ctx, error):
    # チャンネルチェックに失敗した場合は、エラーメッセージを表示せず沈黙する
    if isinstance(error, commands.CheckFailure):
        return
    # その他のエラー（権限不足など）はこれまで通り処理
    if isinstance(error, commands.MissingRole):
        await ctx.send("⚠️ このコマンドを実行する権限がありません。", ephemeral=True)
# ★★★ 新しいチャンネル管理コマンド ★★★
@bot.command()
@commands.has_permissions(administrator=True)
async def register_channel(ctx, channel: discord.TextChannel = None):
    """Botが反応するチャンネルとして現在のチャンネルを登録します。"""
    target_channel = channel or ctx.channel
    guild_id = ctx.guild.id

    load_guild_data(guild_id)
    if target_channel.id in guild_data[guild_id]["channels"]:
        await ctx.send(f"✅ チャンネル <#{target_channel.id}> は既に登録されています。", ephemeral=True)
        return

    guild_data[guild_id]["channels"].add(target_channel.id)
    save_channels(guild_id)
    await ctx.send(f"✅ チャンネル <#{target_channel.id}> を登録しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def unregister_channel(ctx, channel: discord.TextChannel = None):
    """チャンネルの登録を解除します。"""
    target_channel = channel or ctx.channel
    load_channels()

    if target_channel.id not in registered_channel_ids:
        await ctx.send(f"ℹ️ チャンネル <#{target_channel.id}> は登録されていません。", ephemeral=True)
        return

    registered_channel_ids.discard(target_channel.id)
    save_channels()
    await ctx.send(f"✅ チャンネル <#{target_channel.id}> の登録を解除しました。")

@bot.command()
@commands.has_permissions(administrator=True)
async def list_channels(ctx):
    """登録されているチャンネルの一覧を表示します。"""
    load_channels()
    if not registered_channel_ids:
        await ctx.send("現在、登録されているチャンネルはありません。")
        return
    
    embed = discord.Embed(title="📢 登録済みチャンネル一覧", color=discord.Color.blue())
    # <#ID> という形式でチャンネルへのリンクを作成
    channel_links = [f"<#{channel_id}>" for channel_id in registered_channel_ids]
    embed.description = "\n".join(channel_links)
    await ctx.send(embed=embed)

# --- Bot Events ---
@bot.event
async def on_ready():
    # ★ 修正箇所
    global registered_channel_ids # global宣言を追加して、グローバル変数を参照することを明示
    print(f"✅ Botログイン成功: {bot.user}")
    load_budgets()
    load_channels() # ★ 追加: チャンネルリストをロード
    print(f"📢 登録済みチャンネルをロードしました: {registered_channel_ids or '（なし）'}") # これで正しく表示される
    if not os.path.exists(REVIEW_LOG_FILE):
        with open(REVIEW_LOG_FILE, "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["申請者", "購入物", "リンク", "金額", "結果", "承認者", "予算項目"])

@bot.command()
async def request(ctx):
    load_budgets()
    if not budgets:
        await ctx.send("現在、登録されている予算はありません。")
        return
    view = MultiItemRequestView(author=ctx.author)
    embed = view.create_embed()
    await ctx.send(embed=embed, view=view)
    
# (他のコマンド !budget, !add_budget, !send は変更なし)
@bot.command()
async def budget(ctx):
    """現在の全予算の状況を表示する"""
    load_budgets()
    if not budgets:
        await ctx.send("現在、登録されている予算はありません。")
        return
    
    embed = discord.Embed(title="💰 現在の予算状況", color=discord.Color.gold())
    for name, amount in budgets.items():
        embed.add_field(name=name, value=f"¥{amount:,}", inline=False)
    
    await ctx.send(embed=embed)

@bot.command()
@commands.has_role(ALLOWED_ROLE_ID)
async def add_budget(ctx, name: str, amount: int):
    """新しい予算項目を追加、または既存の予算を補充する"""
    load_budgets()
    budgets[name] = budgets.get(name, 0) + amount
    save_budgets()
    await ctx.send(f"✅ 予算「{name}」に ¥{amount:,} を追加しました。現在の残高: ¥{budgets[name]:,}")

@add_budget.error
async def add_budget_error(ctx, error):
    if isinstance(error, commands.MissingRole):
        await ctx.send("⚠️ このコマンドを実行する権限がありません。")

# --- Application Command ---

@bot.command()
async def send(ctx, applicant: str, item: str, link: str, amount: int, budget_name: str):
    """【引数指定用】購入申請を作成する（予算項目付き）"""
    load_budgets()
    
    if budget_name not in budgets:
        await ctx.send(f"⚠️ **エラー:** 予算項目「{budget_name}」は存在しません。")
        return
        
    if amount > budgets[budget_name]:
        await ctx.send(f"⚠️ **エラー:** 予算「{budget_name}」が不足しています。(残高: ¥{budgets[budget_name]:,})")
        return

    embed = discord.Embed(title="購入申請", color=discord.Color.blue())
    embed.add_field(name="申請者", value=applicant, inline=False)
    embed.add_field(name="購入物", value=item, inline=False)
    embed.add_field(name="リンク", value=link, inline=False)
    embed.add_field(name="💰 金額", value=f"¥{amount:,}", inline=True)
    embed.add_field(name="🧾 予算項目", value=budget_name, inline=True)
    embed.set_footer(text="ステータス: 審査中")

    message = await ctx.send(embed=embed)
    await message.add_reaction("✅")
    await message.add_reaction("❌")

    message_data_map[message.id] = {
        "申請者": applicant,
        "購入物": item,
        "リンク": link,
        "金額": amount,
        "予算項目": budget_name,
        "処理済み": False
    }


def save_review_result_partial(applicant, budget_name, approver, approved_items, rejected_items):
    """個別審査の結果をCSVに記録する"""
    with open(REVIEW_LOG_FILE, "a", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        for item in approved_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "承認", approver, budget_name])
        for item in rejected_items:
            writer.writerow([applicant, item["name"], item["link"], item["amount"], "却下", approver, budget_name])

# --- Run the Bot ---
if __name__ == "__main__":
    # Bot起動時に永続Viewを登録
    # bot.add_view(ApprovalView(author=None, items=None, budget_name=None))
    bot.run(TOKEN)