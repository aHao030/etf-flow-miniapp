// services/api.js —— 统一请求层
// 严格按 docs/m2-api-契约.md 实现：
//   - 路径前缀 /api/v1，响应信封 {code, message, data}，code===0 成功；
//   - 金额一律「元」、强度 bp，trade_date 每个接口必返；
//   - mock 模式（config/env.js USE_MOCK）走本地 mock，联调时切换真接口。
const env = require('../config/env');
const mock = require('../mock/index');

function delay(ms) {
  return new Promise(function (resolve) {
    setTimeout(resolve, ms);
  });
}

function normalizeParams(params) {
  const p = params || {};
  const qs = Object.keys(p)
    .filter(function (k) {
      return p[k] !== undefined && p[k] !== null && p[k] !== '';
    })
    .map(function (k) {
      return encodeURIComponent(k) + '=' + encodeURIComponent(p[k]);
    })
    .join('&');
  return qs ? '?' + qs : '';
}

function realRequest(path, params) {
  return new Promise(function (resolve, reject) {
    wx.request({
      url: env.BASE_URL + '/api/v1' + path + normalizeParams(params),
      method: 'GET',
      timeout: 10000,
      success: function (res) {
        const body = res.data || {};
        if (body.code === 0) {
          resolve(body.data);
        } else {
          reject({ code: body.code, message: body.message || '请求失败' });
        }
      },
      fail: function () {
        reject({ code: -1, message: '网络异常，请稍后重试' });
      }
    });
  });
}

function mockRequest(path, params) {
  const body = mock.route(path, params || {});
  return delay(env.MOCK_DELAY_MS).then(function () {
    if (body.code === 0) return body.data;
    return Promise.reject({ code: body.code, message: body.message });
  });
}

function request(path, params) {
  return env.USE_MOCK ? mockRequest(path, params) : realRequest(path, params);
}

module.exports = {
  request: request,
  getMeta: function () {
    return request('/meta');
  },
  getHealth: function () {
    return request('/health');
  },
  getRank: function (params) {
    return request('/rank', params);
  },
  getStocks: function (params) { return request('/stocks', params); },
  getStock: function (code, params) {
    return request('/stock/' + code, params);
  },
  getEtf: function (code, params) {
    return request('/etf/' + code, params);
  },
  getIndustries: function () {
    return request('/industries');
  },
  getEtfs: function (params) {
    return request('/etfs', params);
  }
};
