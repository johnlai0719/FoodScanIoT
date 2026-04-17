import { SQLiteDatabase } from 'expo-sqlite';
import { UserProfile } from '../types';

export const initDatabase = async (db: SQLiteDatabase) => {
  await db.execAsync(`
    CREATE TABLE IF NOT EXISTS scan_history (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      barcode TEXT,
      product_name TEXT,
      score INTEGER,
      grade TEXT,
      timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE IF NOT EXISTS user_profiles (
      id INTEGER PRIMARY KEY DEFAULT 1,
      profile_json TEXT
    );
  `);
};

export const saveUserProfile = async (db: SQLiteDatabase, profile: UserProfile) => {
  try {
    await db.runAsync(
      "INSERT OR REPLACE INTO user_profiles (id, profile_json) VALUES (1, ?)",
      [JSON.stringify(profile)]
    );
    console.log("[StorageService] Profile Saved");
  } catch (e) {
    console.error("[StorageService] Save Profile Error", e);
  }
};

export const loadUserProfile = async (db: SQLiteDatabase): Promise<UserProfile | null> => {
  try {
    const result: any = await db.getFirstAsync("SELECT profile_json FROM user_profiles WHERE id = 1");
    if (result && result.profile_json) {
      console.log("[StorageService] Profile Loaded");
      return JSON.parse(result.profile_json);
    }
    return null;
  } catch (e) {
    console.error("[StorageService] Load Profile Error", e);
    return null;
  }
};

export const saveScanHistory = async (db: SQLiteDatabase, barcode: string, productName: string, score: number, grade: string) => {
  try {
    await db.runAsync(
      "INSERT INTO scan_history (barcode, product_name, score, grade) VALUES (?, ?, ?, ?)",
      [barcode, productName, score, grade]
    );
  } catch (e) {
    console.error("[StorageService] Save History Error", e);
  }
};

export const getScanHistory = async (db: SQLiteDatabase) => {
  try {
    return await db.getAllAsync("SELECT * FROM scan_history ORDER BY timestamp DESC LIMIT 20");
  } catch (e) {
    console.error("[StorageService] Get History Error", e);
    return [];
  }
};
