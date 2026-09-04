# MobiLend

情報企画課向けのモバイル端末貸出管理システムです。`SPECIFICATION.txt` v1.1 に基づき、Flask / SQLite で実装しています。

## 主な機能

- 管理者・一般利用者のログインと権限制御
- 端末台帳、契約プラン、ユーザーの管理
- 貸出、返却、紛失の登録と履歴保持
- 貸出利用者への確認メールと、有効期限付きURLからの返却・紛失・期限延長
- 返却期限超過の動的表示
- 退職済みユーザーのログイン・新規貸出防止
- CSRF対策、パスワードハッシュ、パラメータ化SQL
- スマートフォン対応のレスポンシブ画面

## ローカルで実行

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
$env:MOBILEND_SECRET_KEY = "十分に長いランダム値"
python app.py
```

`http://127.0.0.1:5000` を開いてください。初回起動時に `instance/mobilend.sqlite3` とテストデータを作成します。

| 権限 | ユーザーID | パスワード |
|---|---|---|
| 管理者 | `admin01` | `Admin123!` |
| 利用者 | `user01` | `User123!` |

## メール通知

管理者で「貸出履歴」を開き、貸出中レコードの「メール」を押すと、利用者へ確認URLを発行します。URLは既定で72時間有効で、対象の貸出1件について次の操作だけを許可します。

- 返却登録
- 紛失登録（端末を利用停止）
- 現在より後の日付への返却期限延長

ローカル設定はGit管理外の `instance/local.env` に記述します。初期状態の `MOBILEND_MAIL_MODE=file` では、送信内容を `instance/outbox/*.eml` に保存するため、SMTPなしで確認できます。

実際に送信する場合は、社内メール管理者から案内された値を設定してください。

```dotenv
MOBILEND_MAIL_MODE=smtp
MOBILEND_BASE_URL=https://アプリの公開URL
MOBILEND_MAIL_FROM=mobilend@example.co.jp
MOBILEND_SMTP_HOST=smtp.example.co.jp
MOBILEND_SMTP_PORT=587
MOBILEND_SMTP_USE_TLS=1
MOBILEND_SMTP_USERNAME=ユーザー名
MOBILEND_SMTP_PASSWORD=パスワード
MOBILEND_ACTION_TOKEN_HOURS=72
```

環境変数に同名の設定がある場合は、`instance/local.env` より環境変数を優先します。メールアドレスやSMTP認証情報をGitへコミットしないでください。

## テスト

```powershell
python -m pytest -q
```

## GitHub Pages版について

`docs/` には、GitHub Pagesで公開できる操作デモを収録しています。GitHub Pagesは静的ファイルのみを配信するため、FlaskやSQLiteを実行できません。公開デモではデータをブラウザの `localStorage` に保存し、サーバーへは送信しません。

実運用では、リポジトリ直下のFlaskアプリをHTTPS対応のPythonホスティング環境へ配備してください。GitHub Pages版は画面・操作確認専用です。

`main` ブランチへのpush時に `.github/workflows/pages.yml` が `docs/` を自動公開します。リポジトリの Settings → Pages → Build and deployment で Source を **GitHub Actions** に設定してください。

## 本番運用前の注意

- `MOBILEND_SECRET_KEY` に十分長いランダム値を設定する
- 初期テストアカウントのパスワードを変更または削除する
- HTTPSリバースプロキシ配下で稼働する
- `instance/` とSQLiteファイルのアクセス権を制限する
- Flask内蔵サーバーは開発用途のみにする
