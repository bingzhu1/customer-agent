/**
 * 六种 decision 的展示口径（§9.3）。文案、色调、图标都在这里，组件只查表。
 *
 * 语义色只有四组：琥珀 = 待确认，红 = 拒绝，石板蓝 = 转人工，其余中性。
 */

import type { Confidence, Decision } from '../api/types'
import type { IconName } from '../components/Icon'

export type Tone = 'neutral' | 'info' | 'confirm' | 'human' | 'deny' | 'degrade'

export interface DecisionStyle {
  /** 标签上的中文说明 */
  label: string
  /** CSS 修饰类，见 styles.css 的 .tone-* */
  tone: Tone
  /** 回复下方的说明卡标题；null 表示不需要说明卡 */
  notice: string | null
  icon: IconName
}

export const DECISION_STYLE: Record<Decision, DecisionStyle> = {
  ANSWER: { label: '已回答', tone: 'neutral', notice: null, icon: 'check' },
  REQUEST_INFO: { label: '需补充信息', tone: 'info', notice: '还差关键信息', icon: 'info' },
  REQUIRE_CONFIRMATION: { label: '待你确认', tone: 'confirm', notice: null, icon: 'alert' },
  REQUIRE_HUMAN: { label: '已转人工', tone: 'human', notice: '已转人工处理', icon: 'user' },
  DENY: { label: '已拒绝', tone: 'deny', notice: '无法处理这个请求', icon: 'ban' },
  DEGRADE: { label: '降级回答', tone: 'degrade', notice: '部分信息暂不可用', icon: 'alert' },
}

export function confidenceLabel(confidence: Confidence): string {
  switch (confidence) {
    case 'low':
      return '低'
    case 'high':
      return '高'
    default:
      return '正常'
  }
}

/** 金额是字符串（后端 Decimal 序列化），原样展示，前端不做数值换算。 */
export function formatAmount(amount: string | null, currency: string | null): string {
  if (amount === null) return '—'
  const symbol = currency === 'CNY' ? '¥' : ''
  return `${symbol}${amount}${symbol ? '' : currency ? ` ${currency}` : ''}`
}

export function formatClock(ms: number): string {
  return new Date(ms).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
}
