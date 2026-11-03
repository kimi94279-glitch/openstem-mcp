-- export_document 시험지(exam) 배치용 pandoc 필터. pdf(LaTeX)와 docx에 쓴다.
-- 서버(export.py)가 먼저 구조를 검사해 문제 제목(##)에 openstem-problem, `# 정답과 해설` 제목에
-- openstem-answers 클래스를 붙이고, 풀이 공간 속성 space(cm)를 숫자로 고쳐 둔다. 에러 메시지는 서버가 낸다.
-- 1) 맨 위에 학번·이름 칸, 2) 문제마다 아래에 풀이 공간, 3) 정답과 해설은 새 쪽에서 시작.
local DEFAULT_SPACE = 4 -- cm
local TWIPS_PER_CM = 567
local IS_DOCX = FORMAT:match("docx") ~= nil

local function latex(s) return pandoc.RawBlock("latex", s) end
local function openxml(s) return pandoc.RawBlock("openxml", s) end

local function space_block(cm)
  if cm <= 0 then return nil end
  if IS_DOCX then
    return openxml(string.format('<w:p><w:pPr><w:spacing w:before="%d" w:after="0"/></w:pPr></w:p>',
      math.floor(cm * TWIPS_PER_CM)))
  end
  return latex(string.format("\\vspace{%.1fcm}", cm))
end

local function name_line()
  if IS_DOCX then
    return pandoc.Para({ pandoc.Str("학번:"), pandoc.Space(), pandoc.Str("______________"), pandoc.Space(),
      pandoc.Space(), pandoc.Str("이름:"), pandoc.Space(), pandoc.Str("______________") })
  end
  return latex("\\noindent\\hfill 학번\\ \\underline{\\hspace{3.2cm}}\\qquad 이름\\ \\underline{\\hspace{3.2cm}}"
    .. "\\par\\smallskip\\hrule\\medskip")
end

local function page_break()
  if IS_DOCX then
    return openxml('<w:p><w:r><w:br w:type="page"/></w:r></w:p>')
  end
  -- 정답이 시작하는 쪽 번호를 로그에 남긴다(서버가 읽어 answer_page로 돌려준다).
  return latex("\\clearpage\\typeout{OPENSTEM-ANSWERS=\\thepage}")
end

function Pandoc(doc)
  local out = { name_line() }
  local pending = nil -- 직전 문제 뒤에 둘 풀이 공간(cm)

  local function flush()
    if pending then
      local block = space_block(pending)
      if block then table.insert(out, block) end
      pending = nil
    end
  end

  for _, block in ipairs(doc.blocks) do
    if block.t == "Header" and block.classes:includes("openstem-problem") then
      flush()
      -- 문제 제목이 쪽 맨 아래에 홀로 남지 않게 한다
      if not IS_DOCX then table.insert(out, latex("\\needspace{6\\baselineskip}")) end
      pending = tonumber(block.attributes.space) or DEFAULT_SPACE
    elseif block.t == "Header" and block.classes:includes("openstem-answers") then
      flush()
      table.insert(out, page_break())
    elseif block.t == "Header" and block.level == 1 then
      flush() -- 문제 부분의 큰 제목(예: # 서술형) 앞에서도 앞 문제의 풀이 공간을 닫는다
    end
    table.insert(out, block)
  end
  flush()
  doc.blocks = out
  return doc
end
