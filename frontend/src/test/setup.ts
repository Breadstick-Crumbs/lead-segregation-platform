import '@testing-library/jest-dom/vitest'

// jsdom doesn't lay pages out, so it has no scrollIntoView (Select keeps its highlight in view).
Element.prototype.scrollIntoView = function scrollIntoView() {}
