/** 用户发出的消息。 */

interface Props {
  text: string
}

export default function UserMessageItem({ text }: Props) {
  return (
    <div className="turn user">
      <div className="bubble user-bubble">{text}</div>
    </div>
  )
}
