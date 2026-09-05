/** 用户发出的消息：右对齐的浅色块。 */

interface Props {
  text: string
}

export default function UserMessageItem({ text }: Props) {
  return (
    <div className="turn turn-user">
      <div className="user-msg">{text}</div>
    </div>
  )
}
