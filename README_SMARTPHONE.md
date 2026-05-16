# スマホからアクセスする方法（ngrok）

ngrok を使うと、PC で動かしているサーバーにスマホからインターネット経由でアクセスできます。

## セットアップ手順（初回のみ）

### 1. ngrok アカウント作成
1. https://ngrok.com にアクセスしてアカウントを作成（無料）
2. ダッシュボードにログインし、左メニューの **Your Authtoken** を開く
3. トークン（`2abc...` のような文字列）をコピーしておく

### 2. ngrok インストール
1. https://ngrok.com/download から Windows 版をダウンロード
2. `ngrok.exe` を展開し、`C:\Windows\System32\` またはPATHの通ったフォルダに置く

### 3. 認証トークンを設定
コマンドプロンプトで以下を実行（1回だけでOK）：
```
ngrok config add-authtoken <コピーしたトークン>
```

---

## 使い方

`start.bat` を実行するだけで ngrok が自動起動します。

起動後、コマンドプロンプトに以下のように表示されます：
```
[ngrok] スマホ用トンネルを起動中...
[ngrok] 起動しました。別ウィンドウのngrok画面に表示されるURLをスマホで開いてください。
        例: https://xxxx-xxx-xxx-xxx-xxx.ngrok-free.app
```

ngrok のウィンドウ（タスクバーに最小化されています）を開くと：
```
Forwarding  https://xxxx-xxx-xxx-xxx-xxx.ngrok-free.app -> http://localhost:8000
```
この `https://...` のURLをスマホのブラウザで開けばアクセスできます。

---

## 注意事項

- **PCの電源が入っていてstart.batが動いている間だけ**アクセスできます
- 無料プランではURLがstart.bat起動のたびに変わります
- ngrok 経由のURLは外部に公開されます。APIキー等の機密情報が漏れないよう注意してください（本ツールは社内利用を想定しており問題ありません）
