const fs = require('fs');
const path = require('path');

// 讀取直接下載的 loc_app_direct.js
const jsPath = path.join(__dirname, 'loc_app_direct.js');
if (!fs.existsSync(jsPath)) {
    console.error('❌ 找不到 loc_app_direct.js，請先下載！');
    process.exit(1);
}
const jsCode = fs.readFileSync(jsPath, 'utf8');

// 尋找 e.exports={last_volume:
const startKey = 'e.exports={last_volume:';
const startIdx = jsCode.indexOf(startKey);
if (startIdx === -1) {
    console.error('❌ 找不到 e.exports={last_volume: 起點');
    process.exit(1);
}

const objStart = startIdx + 'e.exports='.length;
let braceCount = 0;
let objEnd = -1;

for (let i = objStart; i < jsCode.length; i++) {
    if (jsCode[i] === '{') {
        braceCount++;
    } else if (jsCode[i] === '}') {
        braceCount--;
        if (braceCount === 0) {
            objEnd = i + 1;
            break;
        }
    }
}

if (objEnd === -1) {
    console.error('❌ 無法配對物件的結尾括號 }');
    process.exit(1);
}

const objText = jsCode.substring(objStart, objEnd);

try {
    // 透過 eval 解析 JavaScript 物件
    const data = eval('(' + objText + ')');
    const outputPath = path.join(__dirname, 'iarc_agents.json');
    fs.writeFileSync(outputPath, JSON.stringify(data, null, 2), 'utf8');
    console.log(`✅ 成功萃取 ${data.agents.length} 筆 IARC 官方致癌分類代理物！`);
    console.log(`💾 輸出路徑: ${outputPath}`);
} catch (err) {
    console.error('❌ 解析或寫入失敗:', err);
    process.exit(1);
}
