module.exports = {
  apps: [
    {
      name: "fog-python-api",
      script: "./venv/bin/python3",
      args: "-m uvicorn main:app --host 0.0.0.0 --port 3002",
      exec_mode: "fork",
      interpreter: "none",
      watch: false,
      env: {
        // 移除硬編碼，讓程式自動讀取 .env 內容
        PYTHON_API_URL: "http://localhost:3002/query"
      }
    },
    {
      name: "fog-node-server",
      script: "npm",
      args: "run dev",
      watch: ["server.ts", "queryHandler.ts", "cache.ts"],
      env: {
        NODE_ENV: "development",
        NODE_PORT: 3001,
        PYTHON_API_URL: "http://localhost:3002/query"
      }
    }
  ]
};
