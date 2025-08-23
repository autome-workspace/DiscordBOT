# bugetBOT
## コマンド一覧
  ### 全員
- `!request`:予算申請
- `!budget`:残り残高表示
- `!list_accounting_roles`:現在設定されているロールを表示

### 会計係権限
- `!add_budget 予算名 金額`:予算設定
- リアクションでの承認・却下

### サーバーの管理者権限
- `!register_channel`:このチャンネルで予算申請できるように登録
- `!unregister_channel`:チャンネル解除
- `!list_channels`:登録チャンネル一覧
- `!add_accounting_role`:サーバーに新しい会計ロールを追加
- `!remove_accounting_role`:サーバーに追加された会計ロールを削除
