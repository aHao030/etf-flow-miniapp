// utils/watchlist.js —— 自选本地存储
// 契约 §2.8 已冻结：M2 不实现服务端自选，前端用本地 Storage。
// 存储结构：[{type:'stock'|'etf', code, name, remind:boolean}]
const KEY = 'watchlist_v1';

function getAll() {
  try {
    return wx.getStorageSync(KEY) || [];
  } catch (e) {
    return [];
  }
}

function save(list) {
  wx.setStorageSync(KEY, list);
}

function has(type, code) {
  return getAll().some(function (it) {
    return it.type === type && it.code === code;
  });
}

function add(item) {
  const list = getAll();
  if (has(item.type, item.code)) return list;
  list.unshift({ type: item.type, code: item.code, name: item.name || item.code, remind: false });
  save(list);
  return list;
}

function remove(type, code) {
  const list = getAll().filter(function (it) {
    return !(it.type === type && it.code === code);
  });
  save(list);
  return list;
}

function toggleRemind(type, code) {
  const list = getAll();
  let state = false;
  const next = list.map(function (it) {
    if (it.type === type && it.code === code) {
      it.remind = !it.remind;
      state = it.remind;
    }
    return it;
  });
  save(next);
  return state;
}

function clearAll() {
  wx.removeStorageSync(KEY);
}

module.exports = { getAll, has, add, remove, toggleRemind, clearAll };
