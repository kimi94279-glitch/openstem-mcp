-- export_document 치트시트(두 단)용 pandoc 필터.
-- pandoc은 표를 longtable로 쓰는데, longtable은 두 단 배치에서 쓸 수 없어 컴파일이 실패한다.
-- 그래서 표를 한 단 폭의 보통 tabular로 바꾼다. 칸 안의 수식·글자는 pandoc이 LaTeX로 쓴 그대로 둔다.
local ALIGN = { AlignLeft = "l", AlignRight = "r", AlignCenter = "c" }

local function cell_latex(cell)
  local doc = pandoc.Pandoc(cell.contents)
  return (pandoc.write(doc, "latex"):gsub("%s*\n%s*", " "))
end

local function rows_latex(rows)
  local out = {}
  for _, row in ipairs(rows) do
    local cells = {}
    for _, cell in ipairs(row.cells) do
      table.insert(cells, cell_latex(cell))
    end
    table.insert(out, table.concat(cells, " & ") .. " \\\\")
  end
  return out
end

function Table(tbl)
  local spec = {}
  for _, colspec in ipairs(tbl.colspecs) do
    table.insert(spec, ALIGN[colspec[1]] or "l")
  end
  local lines = { "\\begin{center}\\small", "\\begin{tabular}{" .. table.concat(spec) .. "}", "\\hline" }
  local head = rows_latex(tbl.head.rows)
  for _, line in ipairs(head) do table.insert(lines, line) end
  if #head > 0 then table.insert(lines, "\\hline") end
  for _, body in ipairs(tbl.bodies) do
    for _, line in ipairs(rows_latex(body.body)) do table.insert(lines, line) end
  end
  table.insert(lines, "\\hline")
  table.insert(lines, "\\end{tabular}")
  table.insert(lines, "\\end{center}")
  return pandoc.RawBlock("latex", table.concat(lines, "\n"))
end
