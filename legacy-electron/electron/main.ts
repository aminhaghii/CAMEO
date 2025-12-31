import { app, BrowserWindow, ipcMain } from 'electron';
import * as path from 'path';
import * as http from 'http';
import * as https from 'https';
import { initDatabases, closeDatabases, getChemicalsDB, getUserDB, saveUserDB } from './database/index.js';

let mainWindow: BrowserWindow | null = null;

async function createWindow() {
  await initDatabases();

  mainWindow = new BrowserWindow({
    width: 1200,
    height: 800,
    webPreferences: {
      preload: path.join(__dirname, '../preload/preload.cjs'),
      contextIsolation: true,
      nodeIntegration: false
    }
  });

  if (process.env.NODE_ENV === 'development') {
    const devUrl = 'http://localhost:5173';
    const devOk = await waitForDevServer(devUrl, 30, 500).catch(() => false);
    if (devOk) {
      await loadRendererWithRetry(devUrl);
      mainWindow.webContents.openDevTools();
    } else {
      await mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
    }
  } else {
    await mainWindow.loadFile(path.join(__dirname, '../renderer/index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
  closeDatabases();
  if (process.platform !== 'darwin') {
    app.quit();
  }
});

app.on('activate', () => {
  if (mainWindow === null) {
    createWindow();
  }
});

async function loadRendererWithRetry(url: string, attempts = 20, delayMs = 500): Promise<void> {
  for (let i = 0; i < attempts; i++) {
    try {
      await mainWindow?.loadURL(url);
      return;
    } catch (err) {
      if (i === attempts - 1) throw err;
      await new Promise((res) => setTimeout(res, delayMs));
    }
  }
}

async function waitForDevServer(url: string, attempts = 30, delayMs = 500): Promise<boolean> {
  const isHttps = url.startsWith('https');
  const client = isHttps ? https : http;

  for (let i = 0; i < attempts; i++) {
    const ok = await new Promise<boolean>((resolve) => {
      const req = client.get(url, (res) => {
        res.resume();
        resolve(res.statusCode !== undefined && res.statusCode >= 200 && res.statusCode < 500);
      });
      req.on('error', () => resolve(false));
      req.end();
    });
    if (ok) return true;
    await new Promise((res) => setTimeout(res, delayMs));
  }
  return false;
}

// IPC Handlers
ipcMain.handle('db:search', async (_event, query: string) => {
  try {
    const db = getChemicalsDB();
    const sql = `
      SELECT id, name, synonyms 
      FROM chemicals 
      WHERE name LIKE ? OR synonyms LIKE ?
      ORDER BY name COLLATE NOCASE
    `;
    const results = db.exec(sql, [`%${query}%`, `%${query}%`]);
    
    if (results.length === 0) {
      return { items: [], total: 0 };
    }

    const items = results[0].values.map(row => ({
      id: row[0] as number,
      name: row[1] as string,
      synonyms: row[2] as string
    }));

    return { items, total: items.length };
  } catch (error) {
    console.error('Search error:', error);
    return { items: [], total: 0 };
  }
});

ipcMain.handle('db:get-chemical', async (_event, id: number) => {
  try {
    const db = getChemicalsDB();
    const sql = `SELECT * FROM chemicals WHERE id = ?`;
    const results = db.exec(sql, [id]);
    
    if (results.length === 0 || results[0].values.length === 0) {
      return null;
    }

    const row = results[0].values[0];
    const columns = results[0].columns;
    
    const chemical: any = {};
    columns.forEach((col, idx) => {
      chemical[col] = row[idx];
    });

    return chemical;
  } catch (error) {
    console.error('Get chemical error:', error);
    return null;
  }
});

ipcMain.handle('db:get-favorites', async () => {
  try {
    const db = getUserDB();
    const results = db.exec('SELECT * FROM favorites ORDER BY added_at DESC');
    
    if (results.length === 0) return [];
    
    return results[0].values.map(row => ({
      id: row[0],
      chemical_id: row[1],
      added_at: row[2],
      note: row[3]
    }));
  } catch (error) {
    console.error('Get favorites error:', error);
    return [];
  }
});

ipcMain.handle('db:add-favorite', async (_event, chemicalId: number, note?: string) => {
  try {
    const db = getUserDB();
    db.run('INSERT INTO favorites (chemical_id, note) VALUES (?, ?)', [chemicalId, note || null]);
    saveUserDB();
    return { success: true };
  } catch (error) {
    console.error('Add favorite error:', error);
    return { success: false, error: String(error) };
  }
});

ipcMain.handle('db:remove-favorite', async (_event, chemicalId: number) => {
  try {
    const db = getUserDB();
    db.run('DELETE FROM favorites WHERE chemical_id = ?', [chemicalId]);
    saveUserDB();
    return { success: true };
  } catch (error) {
    console.error('Remove favorite error:', error);
    return { success: false, error: String(error) };
  }
});
