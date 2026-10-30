/* Yahoo-style multi-source line chart option, shared by the mini program and
 * the local visual harness (tools/preview.html). UMD so both can load it. */
(function (root, factory) {
  if (typeof module === 'object' && module.exports) module.exports = factory()
  else root.ChartOption = factory()
})(typeof self !== 'undefined' ? self : this, function () {
  function pad(value) {
    return String(value).padStart(2, '0')
  }

  var RANGES = [
    { id: '1d', label: '1D', days: 1, interval: 300 },
    { id: '5d', label: '5D', days: 5, interval: 1800 },
    { id: '1m', label: '1M', days: 30, interval: 7200 },
    { id: '6m', label: '6M', days: 180, interval: 43200 },
    { id: '1y', label: '1Y', days: 365, interval: 86400 },
    { id: 'max', label: 'Max', days: 3650, interval: 604800 }
  ]

  function labelFor(iso, interval) {
    var date = new Date(iso)
    if (isNaN(date.getTime())) return ''
    var beijing = new Date(date.getTime() + 8 * 3600000)
    var y = beijing.getUTCFullYear(), m = pad(beijing.getUTCMonth() + 1), d = pad(beijing.getUTCDate())
    var hh = pad(beijing.getUTCHours()), mm = pad(beijing.getUTCMinutes())
    if (interval >= 604800) return String(beijing.getUTCFullYear()).slice(2) + '/' + m
    if (interval >= 86400) return m + '/' + d
    if (interval >= 3600) return m + '/' + d + ' ' + hh + ':00'
    return hh + ':' + mm
  }

  function buildOption(config) {
    var buckets = config.buckets || []
    var series = config.series || []
    var interval = config.interval || 60
    var reverse = !!config.reverse
    var digits = reverse ? 6 : 4
    var bridge = { boc_buy: true, boc_cash: true, daily: true }
    var lines = series.map(function (item) {
      var values = (item.values || []).map(function (value) {
        if (value === null || value === undefined) return null
        return reverse ? 1 / value : value
      })
      return {
        name: item.label,
        type: 'line',
        symbol: 'none',
        showSymbol: false,
        connectNulls: !!bridge[item.source],
        lineStyle: { width: 2, color: item.color },
        itemStyle: { color: item.color },
        emphasis: { focus: 'series' },
        data: values
      }
    })
    var marks = []
    if (config.baseline !== null && config.baseline !== undefined && !reverse) {
      marks.push({
        name: '前收',
        type: 'line',
        yAxis: config.baseline,
        lineStyle: { type: 'dashed', width: 1, color: '#9a9a9a' },
        label: { show: false },
        silent: true
      })
    }
    var option = {
      animation: false,
      color: series.map(function (item) { return item.color }),
      grid: { left: 8, right: 52, top: 30, bottom: 52, containLabel: false },
      legend: {
        top: 0, left: 6, icon: 'roundRect', itemWidth: 12, itemHeight: 3,
        itemGap: 10, textStyle: { fontSize: 10, color: '#5b6b73' }
      },
      tooltip: {
        trigger: 'axis', confine: true,
        axisPointer: { type: 'cross', label: { fontSize: 9 } },
        textStyle: { fontSize: 10 },
        valueFormatter: function (value) {
          return value === null || value === undefined ? '—' : Number(value).toFixed(digits)
        }
      },
      xAxis: {
        type: 'category', boundaryGap: false,
        data: buckets.map(function (iso) { return labelFor(iso, interval) }),
        axisLine: { lineStyle: { color: '#d8e0e3' } },
        axisTick: { show: false },
        axisLabel: { fontSize: 9, color: '#7b8e96', hideOverlap: true }
      },
      yAxis: {
        type: 'value', position: 'right', scale: true,
        splitLine: { lineStyle: { color: '#eef2f4' } },
        axisLine: { show: false },
        axisLabel: {
          fontSize: 9, color: '#7b8e96',
          formatter: function (value) { return Number(value).toFixed(digits) }
        }
      },
      dataZoom: [
        { type: 'inside', xAxisIndex: 0, zoomOnMouseWheel: false, moveOnMouseWheel: false,
          moveOnMouseMove: true, zoomOnPinch: true, throttle: 40 },
        { type: 'slider', xAxisIndex: 0, height: 18, bottom: 6,
          borderColor: 'transparent', backgroundColor: '#f3f6f7',
          fillerColor: 'rgba(26,115,232,0.12)',
          dataBackground: { lineStyle: { color: '#b8c7cc' }, areaStyle: { color: '#e6eef1' } },
          selectedDataBackground: { lineStyle: { color: '#1a73e8' }, areaStyle: { color: '#cfe0f7' } },
          handleSize: 16, showDetail: false }
      ],
      series: lines
    }
    if (marks.length && option.series.length) {
      option.series[0].markLine = { silent: true, symbol: 'none', data: marks }
    }
    return option
  }

  return { buildOption: buildOption, labelFor: labelFor, RANGES: RANGES }
})
