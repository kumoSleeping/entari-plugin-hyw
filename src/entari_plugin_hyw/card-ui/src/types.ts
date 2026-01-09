// Type definitions for render data
// Python only passes raw data, all processing happens in frontend

export interface Stage {
    name: string
    model: string
    provider: string
    icon_name?: string  // Icon identifier (e.g., "google", "openai")
    time: number        // Time in seconds (raw number)
    cost: number        // Cost in dollars (raw number)
    references?: Reference[]
    image_references?: ImageReference[]
    crawled_pages?: CrawledPage[]
}

export interface Reference {
    title: string
    url: string
}

export interface ImageReference {
    title: string
    url: string
    thumbnail?: string
}

export interface CrawledPage {
    title: string
    url: string
}

export interface Stats {
    total_time?: number
    vision_duration?: number
}

export interface Flags {
    has_vision: boolean
    has_search: boolean
}

// Raw data from Python - minimal processing
export interface RenderData {
    markdown: string          // Raw markdown content
    stages: Stage[]
    references: Reference[]   // All references for citation
    page_references: Reference[]
    image_references: ImageReference[]
    stats: Stats
    total_time: number
    theme_color?: string      // Configurable theme color (hex)
}
