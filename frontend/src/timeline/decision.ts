/** 六种 decision 的展示口径（§9.3）。文案与配色都在这里，组件只查表。 */

import type { Decision } from '../api/types'

export interface DecisionStyle {
  /** 徽标上的中文说明 */
  label: string
  /** CSS 修饰类，见 styles.css 的 .tone-* */
  tone: 'neutral' | 'info' | 'confirm' | 'human' | 'deny' | 'degrade'
  /** 回复上方的横幅；null 表示不需要横幅 */
  banner: string | null
}

export const DECISION_STYLE: Record<Decision, DecisionStyle> = {
  ANSWER: { label: '正常回答', tone: 'neutral', banner: null },
  REQUEST_INFO: { label: '需要补充信息', tone: 'info', banner: '还差关键信息，请按提示补充。' },
  REQUIRE_CONFIRMATION: {
    label: '待你确认',
    tone: 'confirm',
    banner: '动作已通过策略判定，等你确认后才会执行。',
  },
  REQUIRE_HUMAN: { label: '已转人工', tone: 'human', banner: '已转人工处理，人工客服会接手这一轮。' },
  DENY: { label: '已拒绝', tone: 'deny', banner: null },
  DEGRADE: { label: '降级回答', tone: 'degrade', banner: '部分信息暂不可用，以下回答基于可获取的部分。' },
}
