# 包裝喵懂 PackMeow

This is an [Expo](https://expo.dev) project created with [`create-expo-app`](https://www.npmjs.com/package/create-expo-app).

包裝喵懂 PackMeow 目前使用 **Expo SDK 58（React Native 0.88）**。手機實機預覽請使用支援 SDK 58 的 Expo Go，或使用以 SDK 58 建立的開發版。

## 白貓添加物問答（Windows 網頁預覽）

電腦需要 LiteRT-LM 和 Gemma 4 E2B 的 `.litertlm` 模型。從 `APP/` 開兩個終端機，
一個啟動 Expo 網頁預覽，另一個啟動只監聽本機的問答服務。模型路徑可換成自己的安裝位置。

```powershell
npm.cmd run web -- --port 8091
```

```powershell
$litert = Join-Path (uv tool dir --bin) 'litert-lm.exe'
python -X utf8 scripts/local_additive_agent.py --litert "$litert" --model 'F:/FoodScanIoT-models/gemma-4-e2b/gemma-4-E2B-it.litertlm'
```

開啟 `http://localhost:8091`，點白貓並輸入「果膠是做什麼的？」或「INS 330 的用途是什麼？」。
這個預覽的本機知識包收錄果膠和檸檬酸，回答下方會顯示 Codex 來源連結。

## Get started

### 白貓導覽素材

從 [Elthen 的白貓套組](https://elthen.itch.io/2d-pixel-art-cat-sprites) 下載
`Cat Sprite Sheet.png`，存成 `APP/assets/images/elthen-cat.png` 後重啟開發伺服器。
圖集留在本機並由 App 打包。若沒有圖集，導覽入口會顯示貓咪圖示。
設計與使用條款見 [Elthen 授權頁](https://www.patreon.com/elthen/posts/licensing-27430241)。

1. Install dependencies

   ```bash
   npm install
   ```

2. Start the app

   ```bash
   npx expo start
   ```

In the output, you'll find options to open the app in a

- [development build](https://docs.expo.dev/develop/development-builds/introduction/)
- [Android emulator](https://docs.expo.dev/workflow/android-studio-emulator/)
- [iOS simulator](https://docs.expo.dev/workflow/ios-simulator/)
- [Expo Go](https://expo.dev/go), a limited sandbox for trying out app development with Expo

You can start developing by editing the files inside the **app** directory. This project uses [file-based routing](https://docs.expo.dev/router/introduction).

## Get a fresh project

When you're ready, run:

```bash
npm run reset-project
```

This command will move the starter code to the **app-example** directory and create a blank **app** directory where you can start developing.

### Other setup steps

- To set up ESLint for linting, run `npx expo lint`, or follow our guide on ["Using ESLint and Prettier"](https://docs.expo.dev/guides/using-eslint/)
- If you'd like to set up unit testing, follow our guide on ["Unit Testing with Jest"](https://docs.expo.dev/develop/unit-testing/)
- Learn more about the TypeScript setup in this template in our guide on ["Using TypeScript"](https://docs.expo.dev/guides/typescript/)

## Learn more

To learn more about developing your project with Expo, look at the following resources:

- [Expo documentation](https://docs.expo.dev/): Learn fundamentals, or go into advanced topics with our [guides](https://docs.expo.dev/guides).
- [Learn Expo tutorial](https://docs.expo.dev/tutorial/introduction/): Follow a step-by-step tutorial where you'll create a project that runs on Android, iOS, and the web.

## Join the community

Join our community of developers creating universal apps.

- [Expo on GitHub](https://github.com/expo/expo): View our open source platform and contribute.
- [Discord community](https://chat.expo.dev): Chat with Expo users and ask questions.
