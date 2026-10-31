/**
 * 資料庫備份複製到 Google 雲端硬碟（Google Apps Script，在使用者自己的 Google 帳號執行）
 *
 * GitHub 每週把兩個資料庫的備份和封存檔放到 ETF 專案 Storage 的 db-backups/<日期>/（見 db_backup.py）。
 * 這支程式每天檢查一次，有新的日期資料夾就整個複製到雲端硬碟「台股資料庫備份/<日期>/」，留最近 KEEP 份
 * 加上最近 MONTHLY_KEEP 個月各月的第一份
 * （舊的移到垃圾桶，30 天後自動刪除）。已經複製過的就不再下載。
 *
 * 設定（只需做一次）：
 *   1. script.google.com 新增專案，貼上這個檔案的內容並儲存。
 *   2. 專案設定 → 指令碼屬性，新增 SUPABASE_URL（ETF 專案網址）、SUPABASE_KEY（ETF 專案的 service_role 金鑰）。
 *   3. 執行一次 setup：授權後會建立每天早上 8～9 點的觸發條件，並馬上複製一次。
 *
 * 還原：同一份的 main.dump.000、.001… 依序接起來（cat main.dump.* > main.dump）再用 pg_restore。
 */
const FOLDER = '台股資料庫備份';
const BUCKET = 'db-backups';
const KEEP = 2;
const MONTHLY_KEEP = 3;  // 另外保留最近 3 個月各月的第一份備份
const DAY = /^\d{4}-\d{2}-\d{2}$/;

function setup() {
  ScriptApp.getProjectTriggers().forEach(t => ScriptApp.deleteTrigger(t));
  ScriptApp.newTrigger('backup').timeBased().everyDays(1).atHour(8).create();
  backup();
}

function backup() {
  const props = PropertiesService.getScriptProperties();
  const base = props.getProperty('SUPABASE_URL').replace(/\/+$/, '') + '/storage/v1';
  const key = props.getProperty('SUPABASE_KEY');
  const headers = {Authorization: 'Bearer ' + key, apikey: key};
  const list = prefix => JSON.parse(UrlFetchApp.fetch(base + '/object/list/' + BUCKET, {
    method: 'post', contentType: 'application/json', headers: headers,
    payload: JSON.stringify({prefix: prefix, limit: 1000}),
  }).getContentText());

  const days = list('').map(o => o.name).filter(n => DAY.test(n)).sort();
  if (!days.length) throw new Error('Supabase 上找不到任何備份');
  const day = days[days.length - 1];

  const root = folder_(DriveApp.getRootFolder(), FOLDER);
  if (root.getFoldersByName(day).hasNext()) {
    console.log(day + ' 已經複製過');
    return;
  }
  // 先放到暫存資料夾，全部下載成功才改成日期名稱，中途失敗下次會重來
  const tmp = root.createFolder(day + '（複製中）');
  list(day + '/').forEach(o => {
    const blob = UrlFetchApp.fetch(base + '/object/' + BUCKET + '/' + day + '/' + o.name, {headers: headers})
      .getBlob().setName(o.name);
    tmp.createFile(blob);
  });
  tmp.setName(day);
  console.log('已複製 ' + day);

  const folders = [];
  const it = root.getFolders();
  while (it.hasNext()) {
    const f = it.next();
    if (DAY.test(f.getName())) folders.push(f);
    else if (f.getName().indexOf('（複製中）') > 0) f.setTrashed(true);  // 之前失敗留下的
  }
  // 留最近 KEEP 份，加上最近 MONTHLY_KEEP 個月各月的第一份（月備份），同 db_backup.py
  folders.sort((a, b) => (a.getName() < b.getName() ? 1 : -1));
  const firstOfMonth = {};
  folders.slice().reverse().forEach(f => {
    const m = f.getName().slice(0, 7);
    if (!(m in firstOfMonth)) firstOfMonth[m] = f.getName();
  });
  const keep = new Set(folders.slice(0, KEEP).map(f => f.getName()));
  Object.keys(firstOfMonth).sort().reverse().slice(0, MONTHLY_KEEP).forEach(m => keep.add(firstOfMonth[m]));
  folders.filter(f => !keep.has(f.getName())).forEach(f => {
    f.setTrashed(true);
    console.log('舊備份移到垃圾桶：' + f.getName());
  });
}

function folder_(parent, name) {
  const it = parent.getFoldersByName(name);
  return it.hasNext() ? it.next() : parent.createFolder(name);
}
