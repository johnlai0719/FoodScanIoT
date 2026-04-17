#!/bin/bash
# 資料庫備份腳本
TIMESTAMP=$(date +"%Y%m%d_%H%M%S")
BACKUP_FILE="/home/johnlai/projects/FoodScanIoT/cloud/backups/product_db_${TIMESTAMP}.sql"

echo "正在備份資料庫到 ${BACKUP_FILE}..."
docker exec foodscaniot-db-1 mysqldump -u root -ppassword product_db > "${BACKUP_FILE}"

if [ $? -eq 0 ]; then
    echo "✅ 備份成功！"
    # 只保留最近 7 天的備份，清理舊檔案
    find /home/johnlai/projects/FoodScanIoT/cloud/backups -name "*.sql" -mtime +7 -delete
else
    echo "❌ 備份失敗，請檢查 Docker 容器狀態。"
fi
