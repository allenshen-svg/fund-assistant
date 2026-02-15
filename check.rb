c = File.read("manager-analysis.html")
start_i = c.index("<script>") + 8
end_i = c.rindex("</script>")
js = c[start_i...end_i]

lines = js.split("\n")
puts "Total JS lines: #{lines.length}"

# Simple approach: find lines with potential syntax issues
# Check for common JS errors
lines.each_with_index do |line, idx|
  ln = idx + 1
  # Check for ` inside template literal that might break
  if line.include?("replace") && line.include?("\\\\")
    puts "Line #{ln}: suspicious escape in replace: #{line.strip[0..120]}"
  end
end

# Check function definitions and closings
func_lines = []
lines.each_with_index do |line, idx|
  if line =~ /^\s*function\s+(\w+)/
    func_lines << {name: $1, line: idx+1}
  end
end
puts "\nFunctions found: #{func_lines.length}"
func_lines.each { |f| puts "  L#{f[:line]}: #{f[:name]}" }

# Look for JavaScript that might crash at parse time
# Check if any obvious syntax errors exist by looking for patterns
lines.each_with_index do |line, idx|
  ln = idx + 1
  # Unterminated string
  sq = line.count("'")
  dq = line.count('"')
  # This is a rough check
end

puts "\nDone."
