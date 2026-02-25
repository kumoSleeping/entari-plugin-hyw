
const stripPrefixBeforeH1 = (text) => {
    const h1Match = text.match(/^#\s+/m)
    if (h1Match && h1Match.index !== undefined) {
        return text.substring(h1Match.index)
    }
    return text
}

const dedent = (text) => {
    const lines = text.split('\n')
    // Find minimum indentation of non-empty lines
    let minIndent = Infinity
    for (const line of lines) {
        if (line.trim().length === 0) continue
        const leadingSpace = line.match(/^\s*/)?.[0].length || 0
        if (leadingSpace < minIndent) minIndent = leadingSpace
    }

    if (minIndent === Infinity || minIndent === 0) return text

    return lines.map(line => {
        if (line.trim().length === 0) return ''
        return line.substring(minIndent)
    }).join('\n')
}

const parse = (rawMd) => {
    if (!rawMd) return []

    const md = stripPrefixBeforeH1(rawMd)

    let content = md.replace(/^#\s+.+$/m, '')
    content = content.replace(/(?:^|\n)\s*(?:#{1,3}|\*\*)\s*(?:References|Citations|Sources)[\s\S]*$/i, '')
    content = content.trim()

    const sections = []

    const combinedRegex = /(```[\s\S]*?```|((?:^|\n)\|[^\n]*\|(?:\n\|[^\n]*\|)*)|<summary>[\s\S]*?<\/summary>)/

    let remaining = content

    while (remaining) {
        const match = remaining.match(combinedRegex)
        if (!match) {
            if (remaining.trim()) {
                sections.push({ type: 'markdown', content: remaining.trim() })
            }
            break
        }

        const index = match.index
        const matchedStr = match[0]
        const preText = remaining.substring(0, index)

        if (preText.trim()) {
            sections.push({ type: 'markdown', content: preText.trim() })
        }

        const isCode = matchedStr.startsWith('```')
        const isSummary = matchedStr.startsWith('<summary>')
        const isTable = !isCode && !isSummary && matchedStr.trim().startsWith('|')

        if (isCode || isTable || isSummary) {
            let language = ''
            let content = matchedStr.trim()

            if (isCode) {
                const match = matchedStr.match(/^```(\w+)/)
                if (match && match[1]) language = match[1]
            } else if (isSummary) {
                content = content.replace(/^<summary>/, '').replace(/<\/summary>$/, '')
                content = dedent(content)
            }

            sections.push({
                type: 'card',
                title: isCode ? 'Code' : (isSummary ? 'Summary' : 'Table'),
                content: content,
                contentType: isCode ? 'code' : (isSummary ? 'summary' : 'table'),
                language: language
            })
        } else {
            sections.push({ type: 'markdown', content: matchedStr })
        }

        remaining = remaining.substring(index + matchedStr.length)
    }

    return sections
}

const test1 = `
# Title

<summary>
    Indented text.
    It might become code block.
</summary>
`

console.log("\n--- Test 2 (After Fix) ---")
console.log(JSON.stringify(parse(test1), null, 2))
