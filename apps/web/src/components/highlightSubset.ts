import type { Element, ElementContent, Root } from 'hast'
import { toText } from 'hast-util-to-text'
import { createLowlight } from 'lowlight'
import { visit } from 'unist-util-visit'
// 语言白名单：本产品的代码块只会来自 SQL 查询、沙箱 Python、终端命令、
// 配置/文档——未注册的语言按纯文本渲染（与上游 detect:false 默认一致）。
import bash from 'highlight.js/lib/languages/bash'
import diff from 'highlight.js/lib/languages/diff'
import dockerfile from 'highlight.js/lib/languages/dockerfile'
import ini from 'highlight.js/lib/languages/ini'
import javascript from 'highlight.js/lib/languages/javascript'
import json from 'highlight.js/lib/languages/json'
import markdown from 'highlight.js/lib/languages/markdown'
import python from 'highlight.js/lib/languages/python'
import sql from 'highlight.js/lib/languages/sql'
import typescript from 'highlight.js/lib/languages/typescript'
import xml from 'highlight.js/lib/languages/xml'
import yaml from 'highlight.js/lib/languages/yaml'

const highlightLanguages = {
  bash,
  diff,
  dockerfile,
  ini,
  javascript,
  json,
  markdown,
  python,
  sql,
  typescript,
  xml,
  yaml,
}

const lowlight = createLowlight(highlightLanguages)

/**
 * rehype-highlight 的裁剪替代。上游插件模块顶层 `import {common} from 'lowlight'`
 * 并把 common（37 种语言、约 390 KiB 源码）用在 `settings.languages || common`
 * 兜底表达式里——即便运行时传了 languages 选项，bundler 也无法把 common 摇掉。
 * 这里只注册白名单语言（约 90 KiB），逻辑与上游等价：只处理带 language-* 类名
 * 的 <pre><code>，命中已注册语言才替换为带高亮 span 的子树。
 */
export function rehypeHighlightSubset() {
  return (tree: Root) => {
    visit(tree, 'element', (node: Element, _index, parent) => {
      if (
        node.tagName !== 'code'
        || !parent
        || parent.type !== 'element'
        || (parent as Element).tagName !== 'pre'
      ) return

      const className = Array.isArray(node.properties?.className) ? node.properties.className : []
      const languageClass = className.find(
        (name) => typeof name === 'string' && name.startsWith('language-'),
      )
      if (!languageClass) return
      const language = String(languageClass).slice('language-'.length)
      if (!lowlight.registered(language)) return

      const result = lowlight.highlight(language, toText(node, { whitespace: 'pre-wrap' }))
      // lowlight 高亮结果只含 element/text 节点，类型上却是 RootContent（含 doctype）
      node.children = result.children as ElementContent[]

      const parentElement = parent as Element
      const parentClassName = Array.isArray(parentElement.properties?.className)
        ? parentElement.properties.className
        : []
      parentElement.properties = {
        ...parentElement.properties,
        className: [...parentClassName, 'hljs', `language-${language}`],
      }
    })
  }
}
