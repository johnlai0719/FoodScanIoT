import express, { Request, Response, NextFunction } from 'express';
import cors from 'cors';
import dotenv from 'dotenv';
import mysql from 'mysql2/promise';
import { CloudAnalyzeRequest } from '../shared/types';
import { processModuleA } from './modules/moduleA';
import { processModuleB } from './modules/moduleB';
import { processModuleC } from './modules/moduleC';
import { processModuleD } from './modules/moduleD';

dotenv.config();

const app = express();
const PORT = process.env.PORT || 3002;

export const dbConfig = {
  host: process.env.DB_HOST || 'localhost',
  user: process.env.DB_USER || 'root',
  password: process.env.DB_PASSWORD || '',
  database: process.env.DB_NAME || 'product_db',
  waitForConnections: true,
  connectionLimit: 10,
  queueLimit: 0
};

export const pool = mysql.createPool(dbConfig);

app.use(cors());
app.use(express.json({ limit: '10mb' }));

app.get('/health', async (req, res) => {
  try {
    await pool.query('SELECT 1');
    res.json({ status: 'ok', db: 'connected' });
  } catch (err) {
    res.status(500).json({ status: 'error', db: 'disconnected' });
  }
});

/**
 * [Main Workflow] Cloud Analyze
 */
app.post('/analyze', async (req: Request, res: Response, next: NextFunction) => {
  try {
    const { barcode, product_info, ingredients, additives, nutrition, user_conditions } = req.body as CloudAnalyzeRequest;
    
    // 1. Module A: Preprocessing & Entity Alignment
    const moduleA = await processModuleA(ingredients, additives, nutrition);
    
    // 2. Module B: Health Risk Engine
    const moduleB = await processModuleB(moduleA.nutrition_vector, moduleA.matched_additives, user_conditions);
    
    // 3. Module C: Food Safety Risk Engine
    const moduleC = await processModuleC(barcode, product_info.manufacturer);
    
    // 4. Module D: Fusion & Output
    const finalResult = processModuleD(barcode, product_info, moduleB, moduleC, user_conditions);
    
    res.json(finalResult);
  } catch (err) {
    next(err);
  }
});

app.use((err: any, req: Request, res: Response, next: NextFunction) => {
  console.error(err);
  res.status(500).json({ error: err.message });
});

app.listen(PORT, () => {
  console.log(`☁️ Cloud Server (Core Brain) is running on http://localhost:${PORT}`);
});
