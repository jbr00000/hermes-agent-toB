import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { rehypeHighlightSubset } from './highlightSubset'
// 浅色高亮主题，匹配纸面风格；.markdown-body 里会把 pre code 背景透明化
import 'highlight.js/styles/github.css'

/**
 * 共享 Markdown 渲染组件：chat 气泡（MessageBubble）与计划面板（TaskPlanPanel）共用。
 * - remark-gfm：表格 / 删除线 / 任务列表 / 自动链接（裸 react-markdown 默认关闭 GFM，
 *   管道表格会渲染成原始文本）
 * - rehypeHighlightSubset：代码块语法高亮（语言白名单见 highlightSubset.ts，
 *   未注册的语言按纯文本渲染）。不用上游 rehype-highlight：它静态引用 lowlight
 *   的 common 全集（约 390 KiB），传 languages 选项也摇不掉
 * - 样式在 styles.css 的 .markdown-body 块（表格边框、引用块、hr 等）
 * react-markdown 默认转义原始 HTML，安全现状不变。
 */
export function Markdown({ content, className }: { content: string; className?: string }): React.ReactElement {
  return (
    <div className={`markdown-body break-words${className ? ` ${className}` : ''}`}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        rehypePlugins={[rehypeHighlightSubset]}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
}
