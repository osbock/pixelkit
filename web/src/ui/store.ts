// Remember the last chosen picture file across visits (IndexedDB), so a
// reload doesn't lose it. Everything here is best-effort.
const DB = "pixelkit", STORE = "picture", KEY = "last";

export interface SavedPicture { name: string; id: string; type: string; bytes: Uint8Array }

function open(): Promise<IDBDatabase> {
  return new Promise((resolve, reject) => {
    const req = indexedDB.open(DB, 1);
    req.onupgradeneeded = () => req.result.createObjectStore(STORE);
    req.onsuccess = () => resolve(req.result);
    req.onerror = () => reject(req.error);
  });
}

export async function savePicture(p: SavedPicture | null): Promise<void> {
  try {
    const db = await open();
    await new Promise<void>((resolve, reject) => {
      const tx = db.transaction(STORE, "readwrite");
      if (p) tx.objectStore(STORE).put(p, KEY);
      else tx.objectStore(STORE).delete(KEY);
      tx.oncomplete = () => resolve();
      tx.onerror = () => reject(tx.error);
    });
    db.close();
  } catch { /* private mode or storage disabled */ }
}

export async function loadPicture(): Promise<SavedPicture | null> {
  try {
    const db = await open();
    const value = await new Promise<SavedPicture | undefined>((resolve, reject) => {
      const req = db.transaction(STORE).objectStore(STORE).get(KEY);
      req.onsuccess = () => resolve(req.result as SavedPicture | undefined);
      req.onerror = () => reject(req.error);
    });
    db.close();
    return value ?? null;
  } catch {
    return null;
  }
}
