const app = getApp()
const { request } = require('../../utils/request')
const ChartOption = require('../../utils/chart-option')
const echarts = require('../../components/ec-canvas/echarts')
const pad = n => String(n).padStart(2, '0')

function beijingDate(date) {
  const d = new Date(date.getTime() + 8 * 3600000)
  return { date: `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`, time: `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}` }
}

const RANGES = ChartOption.RANGES

Page({
  data: {
    tab: 'chart', error: '', quotePrice: '—', quoteTime: '等待报价', quoteLabel: '中行现汇买入', quoteStale: false,
    pairs: [], pairIndex: 0, pair: 'USD', pairLabel: '美元',
    ranges: RANGES.map(r => r.label), rangeIndex: 0,
    sources: [], reverse: false, pointCount: 0,
    rules: [], alerts: [], start: '08:00', end: '17:00', kind: 'movement', amount: '0.01',
    directions: ['达到或高于目标价', '达到或低于目标价'], directionIndex: 0,
    saving: false, aiBusy: false, notificationsReady: false,
    ec: { onInit: null }
  },
  onLoad() {
    const end = beijingDate(new Date()), start = beijingDate(new Date(Date.now() - 24 * 3600000))
    this.setData({ fromDate: start.date, fromTime: start.time, toDate: end.date, toTime: end.time })
    this.setData({
      ec: {
        onInit: (canvas, width, height, dpr) => {
          const chart = echarts.init(canvas, null, { width: width, height: height, devicePixelRatio: dpr })
          canvas.setChart(chart)
          this.chart = chart
          this.drawChart()
          return chart
        }
      }
    })
    this.refresh()
  },
  onShow() { this.timer = setInterval(() => this.refreshStatus().catch(() => {}), 60000) },
  onHide() { clearInterval(this.timer) },
  onUnload() { clearInterval(this.timer) },
  async login() {
    if (this.loginPending) return this.loginPending
    this.loginPending = (async () => {
      let code
      if (app.globalData.demo) {
        code = wx.getStorageSync('demoIdentity') || `demo-${Date.now()}-${Math.random()}`
        wx.setStorageSync('demoIdentity', code)
      } else {
        code = (await new Promise((resolve, reject) => wx.login({ success: resolve, fail: reject }))).code
      }
      const result = await this.http('/api/login', 'POST', { code }, false)
      if (result.demo !== app.globalData.demo) throw new Error('前后端演示模式不一致，请检查配置')
      this.token = result.token
    })()
    try { await this.loginPending } finally { this.loginPending = null }
  },
  http(path, method = 'GET', data, authenticated = true) {
    return request(app.globalData, path, method, data, authenticated ? this.token : null)
      .then(r => {
        if (r.statusCode === 401) this.token = null
        if (r.statusCode >= 200 && r.statusCode < 300) return r.data
        throw new Error((r.data && r.data.error) || `服务错误 ${r.statusCode}`)
      })
  },
  async api(path, method, data) {
    if (!this.token) await this.login()
    return this.http(path, method, data)
  },
  showError(e) { this.setData({ error: e.message || '操作失败，请重试' }) },
  rangeConfig() { return RANGES[this.data.rangeIndex] || RANGES[0] },
  isoBefore(days) { return new Date(Date.now() - days * 86400000).toISOString() },
  async refresh() {
    this.setData({ error: '' })
    try {
      await this.loadPairs()
      await this.refreshStatus()
      await Promise.all([this.loadTable(), this.loadChart(), this.loadRules(), this.loadAlerts()])
    } catch (e) { this.showError(e) }
  },
  async loadPairs() {
    const data = await this.api('/api/pairs')
    const pairs = data.pairs || []
    let index = pairs.findIndex(item => item.pair === this.data.pair)
    if (index < 0) index = 0
    const current = pairs[index] || { pair: 'USD', label: '美元' }
    this.setData({ pairs: pairs.map(item => item.pair), pairsInfo: pairs, pairIndex: index, pair: current.pair, pairLabel: current.label })
  },
  pairTap(e) {
    const target = e.currentTarget.dataset.pair
    const index = (this.data.pairsInfo || []).findIndex(item => item.pair === target)
    if (index >= 0) this.pairChange({ detail: { value: index } })
  },
  rangeTap(e) {
    this.setData({ rangeIndex: Number(e.currentTarget.dataset.index) })
    this.loadChart().catch(err => this.showError(err))
  },
  pairChange(e) {
    const index = Number(e.detail.value)
    const current = (this.data.pairsInfo || [])[index]
    if (!current) return
    this.setData({ pairIndex: index, pair: current.pair, pairLabel: current.label, quotePrice: '—', quoteTime: '加载中…', tab: 'chart' })
    this.refreshStatus().then(() => Promise.all([this.loadTable(), this.loadChart()])).catch(err => this.showError(err))
  },
  rangeChange(e) {
    this.setData({ rangeIndex: Number(e.detail.value) })
    this.loadChart().catch(err => this.showError(err))
  },
  reverseChange() { this.setData({ reverse: !this.data.reverse }); this.drawChart(); this.renderTable() },
  async refreshStatus() {
    const data = await this.api('/api/table?pair=' + this.data.pair)
    this.setData({ status: data, notificationsReady: (this.data.status || {}).notifications_ready })
    this.renderTable(data)
  },
  renderTable(table) {
    table = table || this.data.status
    if (!table || !table.sources) return
    const reverse = this.data.reverse
    const rows = table.sources.map(row => {
      let price = '—', change = '—', changeClass = ''
      if (row.price !== null && row.price !== undefined) {
        price = (reverse ? 1 / row.price : row.price).toFixed(reverse ? 6 : 4)
        if (row.change !== null && row.change !== undefined) {
          const value = reverse ? (1 / row.price) - (1 / (row.price - row.change)) : row.change
          change = value.toFixed(reverse ? 6 : 4)
          changeClass = value > 0 ? 'up' : value < 0 ? 'down' : ''
        }
      }
      return {
        ...row, displayPrice: price, displayChange: change, changeClass,
        displayTime: row.stamp ? row.stamp.slice(5, 16).replace('T', ' ') : '—'
      }
    })
    const primary = rows.find(row => row.price !== null) || {}
    this.setData({
      sources: rows,
      quoteLabel: (primary.label || '—') + ' · ' + table.label,
      quotePrice: primary.displayPrice || '—',
      quoteTime: primary.stamp ? primary.stamp.slice(0, 19).replace('T', ' ') + ' 北京时间' : '等待报价',
      quoteStale: !!primary.stale,
      unitLabel: reverse ? '外币' : '元'
    })
  },
  async loadTable() {
    const data = await this.api('/api/table?pair=' + this.data.pair)
    this.setData({ status: data })
    this.renderTable(data)
  },
  async loadChart() {
    const config = this.rangeConfig()
    const data = await this.api('/api/chart?pair=' + this.data.pair + '&start=' + encodeURIComponent(this.isoBefore(config.days)) +
      '&end=' + encodeURIComponent(new Date().toISOString()) + '&interval=' + config.interval)
    this.chartData = data
    this.setData({ pointCount: (data.buckets || []).length, rangeLabel: config.label })
    this.drawChart()
  },
  drawChart() {
    if (!this.chart || !this.chartData) return
    let baseline = null
    const table = this.data.status
    if (table && table.sources) {
      const quote = table.sources.find(row => row.price !== null && row.change !== null)
      if (quote) baseline = quote.price - quote.change
    }
    this.chart.setOption(ChartOption.buildOption({
      buckets: this.chartData.buckets,
      series: this.chartData.series,
      interval: (this.chartData.interval || 60),
      reverse: this.data.reverse,
      baseline: baseline
    }), true)
  },
  selectTab(e) {
    this.setData({ tab: e.currentTarget.dataset.tab })
    if (e.currentTarget.dataset.tab === 'alerts') this.loadAlerts().catch(err => this.showError(err))
  },
  startChange(e) { this.setData({ start: e.detail.value }) },
  endChange(e) { this.setData({ end: e.detail.value }) },
  kindChange(e) { this.setData({ kind: e.detail.value, amount: e.detail.value === 'movement' ? '0.01' : '6.70' }) },
  amountChange(e) { this.setData({ amount: e.detail.value }) },
  directionChange(e) { this.setData({ directionIndex: Number(e.detail.value) }) },
  async loadRules() { const r = await this.api('/api/rules'); this.setData({ rules: r.rules }) },
  async saveRule() {
    if (this.data.saving) return
    this.setData({ saving: true, error: '' })
    try {
      const d = this.data
      if (!(Number(d.amount) > 0 && Number(d.amount) < 100)) throw new Error('请输入大于 0、小于 100 的金额')
      const primary = (d.sources || []).find(row => row.price !== null) || {}
      await this.api('/api/rules', 'POST', {
        kind: d.kind, start: d.start, end: d.end, amount: d.amount,
        direction: d.directionIndex === 0 ? 'above' : 'below',
        source: primary.source || 'boc_buy', pair: d.pair
      })
      await this.loadRules()
      wx.showToast({ title: '规则已保存' })
    } catch (e) { this.showError(e) } finally { this.setData({ saving: false }) }
  },
  async deleteRule(e) {
    const answer = await new Promise(resolve => wx.showModal({ title: '删除监测', content: '确定删除这条规则和对应触发记录？', success: resolve }))
    if (!answer.confirm) return
    try { await this.api('/api/rules/' + e.currentTarget.dataset.id, 'DELETE'); await this.loadRules() } catch (e) { this.showError(e) }
  },
  subscribe() {
    const template = (this.data.status || {}).template_id
    if (!this.data.status || !this.data.status.notifications_ready || !template) return this.showError(new Error('请先在服务端接入微信订阅消息'))
    wx.requestSubscribeMessage({ tmplIds: [template], success: async result => {
      try {
        if (result[template] !== 'accept') throw new Error('未获得订阅授权，可在微信设置中检查权限')
        await this.api('/api/subscriptions', 'POST', { accepted: true })
        await this.loadTable()
        wx.showToast({ title: '已记录本次授权' })
      } catch (e) { this.showError(e) }
    }, fail: () => this.showError(new Error('订阅授权失败，请检查模板和小程序权限')) })
  },
  async loadAlerts() {
    const r = await this.api('/api/alerts')
    const names = { pending: '待发送', sending: '发送结果待核实', sent: '已请求微信发送', failed: '发送失败/未知', needs_subscription: '需重新授权', expired: '已过期' }
    const sides = { up: '上涨达标', down: '下跌达标', above: '达到目标上限', below: '达到目标下限' }
    this.setData({ alerts: r.alerts.map(a => ({
      ...a, label: sides[a.side], statusLabel: names[a.status] || a.status,
      displayTime: a.stamp.slice(0, 19).replace('T', ' '),
      sourceLabel: a.pair_label ? a.pair_label + ' · ' : ''
    })) })
  }
})
