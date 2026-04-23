module.exports = {
  apps: [
    {
      name: 'Cloud-Server',
      script: 'uvicorn',
      args: 'main:app --host 0.0.0.0 --port 3002 --reload',
      cwd: './server',
      env_file: '.env',
      interpreter: 'python3',
      env: {
        DB_HOST: 'localhost',
        DATABASE_URL: 'mysql+pymysql://root:password@localhost:3306/product_db?charset=utf8mb4'
      }
    },
    {
      name: 'Admin-Manager',
      script: 'uvicorn',
      args: 'admin_main:app --host 0.0.0.0 --port 3003 --reload',
      cwd: './server',
      env_file: '.env',
      interpreter: 'python3',
      env: {
        DB_HOST: 'localhost',
        DATABASE_URL: 'mysql+pymysql://root:password@localhost:3306/product_db?charset=utf8mb4'
      }
    },
    {
      name: 'Cloud-Connect-Node',
      script: 'cloudflared',
      args: 'tunnel --url http://localhost:3002',
      env: {
        NODE_ENV: 'production'
      }
    }
  ]
};
