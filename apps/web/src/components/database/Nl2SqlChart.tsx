import * as React from 'react'
import { BarChart, LineChart, PieChart } from 'echarts/charts'
import { DataZoomComponent, GridComponent, TooltipComponent } from 'echarts/components'
import * as echarts from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { Nl2sqlFormatOutput } from '../../types'

echarts.use([BarChart, LineChart, PieChart, GridComponent, TooltipComponent, DataZoomComponent, CanvasRenderer])

/** 类目超过该数时柱/线图加 dataZoom 滑块（对齐 lone-ai 前端的浏览体验口径） */
const DATA_ZOOM_THRESHOLD = 12

const AXIS_COLOR = '#a1a1aa'
const SPLIT_LINE = '#ececef'
const BAR_COLOR = '#3f3f46'

function toNumber(value: unknown): number {
  const num = Number(value)
  return Number.isFinite(num) ? num : 0
}

/** 问数结果卡的内嵌图表：后端已按 figureType/dimensions/metrics 抽好 dataFigure，
 *  这里只做渲染；figureType ∉ {pie,bar,line} 或数据为空时不渲染（调用方判断）。 */
export function Nl2SqlChart({ output }: { output: Nl2sqlFormatOutput }) {
  const containerRef = React.useRef<HTMLDivElement | null>(null)

  const dimension = output.dimensions
  const metric = output.metrics
  const rows = output.dataFigure

  React.useEffect(() => {
    const container = containerRef.current
    if (!container || !dimension || !metric || rows.length === 0) return

    const chart = echarts.init(container)
    const categories = rows.map((row) => String(row[dimension] ?? ''))
    const values = rows.map((row) => toNumber(row[metric]))

    if (output.figureType === 'pie') {
      chart.setOption({
        tooltip: { trigger: 'item', valueFormatter: (value: unknown) => String(value) },
        series: [{
          type: 'pie',
          radius: ['38%', '68%'],
          center: ['50%', '52%'],
          label: { color: '#52525b', fontSize: 12, formatter: '{b}: {c} ({d}%)' },
          itemStyle: { borderColor: '#fff', borderWidth: 1 },
          data: categories.map((name, index) => ({ name, value: values[index] })),
        }],
      })
    } else {
      const isLine = output.figureType === 'line'
      chart.setOption({
        grid: { left: 12, right: 16, top: 28, bottom: categories.length > DATA_ZOOM_THRESHOLD ? 52 : 28, containLabel: true },
        tooltip: { trigger: 'axis' },
        xAxis: {
          type: 'category',
          data: categories,
          axisLabel: { color: AXIS_COLOR, fontSize: 11, rotate: categories.some((item) => item.length > 6) ? 30 : 0 },
          axisLine: { lineStyle: { color: SPLIT_LINE } },
          axisTick: { show: false },
        },
        yAxis: {
          type: 'value',
          name: metric,
          nameTextStyle: { color: AXIS_COLOR, fontSize: 11 },
          axisLabel: { color: AXIS_COLOR, fontSize: 11 },
          splitLine: { lineStyle: { color: SPLIT_LINE } },
        },
        series: [{
          type: isLine ? 'line' : 'bar',
          data: values,
          barMaxWidth: 36,
          itemStyle: { color: BAR_COLOR, borderRadius: isLine ? 0 : [3, 3, 0, 0] },
          lineStyle: isLine ? { color: BAR_COLOR, width: 2 } : undefined,
          symbolSize: 6,
        }],
        ...(categories.length > DATA_ZOOM_THRESHOLD
          ? {
              dataZoom: [
                { type: 'slider', height: 18, bottom: 8, borderColor: SPLIT_LINE, fillerColor: 'rgba(63,63,70,0.12)' },
                { type: 'inside' },
              ],
            }
          : {}),
      })
    }

    const observer = new ResizeObserver(() => chart.resize())
    observer.observe(container)
    return () => {
      observer.disconnect()
      chart.dispose()
    }
  }, [output.figureType, dimension, metric, rows])

  if (!dimension || !metric || rows.length === 0) return null
  return <div ref={containerRef} className="mt-2 h-72 w-full" />
}
