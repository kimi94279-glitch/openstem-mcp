-- export_document 낱말 카드(flashcards) 배치용 pandoc 필터. LaTeX(pdf) 전용.
-- 문서 안 2칸 표의 각 행(머리 행 제외)을 카드 한 장으로 보고, A4 한 쪽에 8장(2열 x 4행)씩
-- 앞면 쪽과 뒷면 쪽을 번갈아 만든다. 뒷면 쪽은 좌우를 뒤집어 놓아서, 양면 인쇄(긴 쪽 넘김)하면
-- 각 카드의 앞뒤가 맞는다. 표 밖 글은 버린다(서버가 미리 검사하고 경고한다).
-- 표 위의 제목(#, ##, ...)은 그 아래 카드들의 주제로 앞면 귀퉁이에 작게 쓴다.
local COLS, ROWS = 2, 4
local W, H = 9, 6.5 -- 카드 크기(cm)
local PAGE_W, PAGE_H = 21, 29.7
local X0 = (PAGE_W - COLS * W) / 2 -- 왼쪽 여백 1.5cm
local Y0 = (PAGE_H - ROWS * H) / 2 -- 위쪽 여백 1.85cm
local PER_PAGE = COLS * ROWS

local function trim(s)
  return (s:gsub("^%s+", ""):gsub("%s+$", ""))
end

local function to_latex(blocks)
  return trim(pandoc.write(pandoc.Pandoc(blocks), "latex"))
end

-- 카드 속 그림은 카드 안에 들어가게 크기를 정해 둔다(pandoc이 width·height를 \includegraphics에 쓴다).
local function fit_images(blocks)
  return pandoc.Blocks(blocks):walk({
    Image = function(img)
      img.attributes.width = "7cm"
      img.attributes.height = "4cm"
      return img
    end,
  })
end

local function card_node(x, y, number, size, content)
  return string.format("\\node[inner sep=0pt] at (%.2f,%.2f) {\\cardtext{%d}{%s}{%s}};",
    x + W / 2, y - H / 2, number, size, content)
end

local function page(cards, first, last, side, label)
  local lines = {
    "\\begin{tikzpicture}[x=1cm,y=1cm]",
    string.format("\\useasboundingbox (0,0) rectangle (%.1f,%.1f);", PAGE_W, -(PAGE_H - 0.1)),
    string.format("\\node[anchor=south west,font=\\footnotesize\\sffamily,text=gray] at (%.2f,%.2f) {%s};",
      X0, -Y0 + 0.25, label),
  }
  for i = first, last do
    local card = cards[i]
    local k = i - first
    local col, row = k % COLS, k // COLS
    if side == "back" then col = COLS - 1 - col end -- 좌우 뒤집기
    local x, y = X0 + col * W, -(Y0 + row * H)
    table.insert(lines, string.format("\\draw[gray!60,dashed] (%.2f,%.2f) rectangle ++(%.1f,%.1f);", x, y, W, -H))
    table.insert(lines, string.format(
      "\\node[anchor=north east,font=\\scriptsize\\sffamily,text=gray] at (%.2f,%.2f) {%d};", x + W - 0.2, y - 0.15, i))
    if side == "front" then
      if card.topic ~= "" then
        table.insert(lines, string.format(
          "\\node[anchor=north west,font=\\scriptsize\\sffamily,text=gray,text width=7cm] at (%.2f,%.2f) {%s};",
          x + 0.2, y - 0.15, card.topic))
      end
      table.insert(lines, card_node(x, y, i, "\\Large", card.front))
    else
      table.insert(lines, card_node(x, y, i, "\\large", card.back))
    end
  end
  table.insert(lines, "\\end{tikzpicture}")
  table.insert(lines, "\\newpage")
  return pandoc.RawBlock("latex", table.concat(lines, "\n"))
end

function Pandoc(doc)
  local cards, topic = {}, ""
  for _, block in ipairs(doc.blocks) do
    if block.t == "Header" then
      topic = to_latex({ pandoc.Plain(block.content) })
    elseif block.t == "Table" and #block.colspecs == 2 then
      for _, body in ipairs(block.bodies) do
        for _, row in ipairs(body.body) do
          local front = to_latex(fit_images(row.cells[1].contents))
          local back = to_latex(fit_images(row.cells[2].contents))
          if front ~= "" or back ~= "" then
            table.insert(cards, { front = front, back = back, topic = topic })
          end
        end
      end
    end
  end

  local title = ""
  if doc.meta.title then
    title = " · " .. to_latex({ pandoc.Plain(doc.meta.title) })
  end
  local sheets = math.max(1, math.ceil(#cards / PER_PAGE))
  local out = {}
  for s = 1, sheets do
    local first = (s - 1) * PER_PAGE + 1
    local last = math.min(s * PER_PAGE, #cards)
    local count = string.format("%d/%d", s, sheets)
    table.insert(out, page(cards, first, last, "front", "앞면 " .. count .. title))
    table.insert(out, page(cards, first, last, "back",
      "뒷면 " .. count .. " · 양면 인쇄(긴 쪽 넘김)하면 앞면과 맞는다. 점선을 따라 자른다."))
  end
  doc.blocks = out
  return doc
end
