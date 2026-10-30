App({
  onLaunch() {
    if (this.globalData.transport === 'cloud' && wx.cloud && wx.cloud.init) {
      // 云托管 callContainer 不依赖云开发环境；无默认云开发环境时 init 可能抛错，忽略即可。
      try { wx.cloud.init() } catch (_) {}
    }
  },
  globalData: {
    transport: 'cloud',
    cloudEnv: 'prod-d9gjkq67xd89186c3',
    cloudService: 'exchange-rate-api',
    apiBase: 'http://127.0.0.1:8000',
    demo: false
  }
})
