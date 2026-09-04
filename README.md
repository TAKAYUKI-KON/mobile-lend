# MobiLend

情報企画課向けのモバイル端末貸出管理システムです。`SPECIFICATION.txt` v1.1 に基づき、Flask / SQLite で実装しています。

## 主な機能

- 管理者・一般利用者のログインと権限制御
- 端末台帳、契約プラン、ユーザーの管理
- 貸出、返却、紛失の登録と履歴保持
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
