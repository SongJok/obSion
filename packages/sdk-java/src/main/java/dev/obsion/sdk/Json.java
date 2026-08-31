package dev.obsion.sdk;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/** Minimal JSON codec for REST maps. Production credentials must never appear in payloads. */
final class Json {
  private Json() {}

  static String stringify(Object value) {
    StringBuilder out = new StringBuilder();
    write(out, value);
    return out.toString();
  }

  static Object parse(String text) {
    return new Parser(text).parseValue();
  }

  @SuppressWarnings("unchecked")
  static Map<String, Object> asObject(Object value) {
    if (value instanceof Map<?, ?> map) {
      return (Map<String, Object>) map;
    }
    throw new IllegalArgumentException("JSON object expected");
  }

  @SuppressWarnings("unchecked")
  static List<Object> asArray(Object value) {
    if (value instanceof List<?> list) {
      return (List<Object>) list;
    }
    throw new IllegalArgumentException("JSON array expected");
  }

  private static void write(StringBuilder out, Object value) {
    switch (value) {
      case null -> out.append("null");
      case String text -> writeString(out, text);
      case Boolean flag -> out.append(flag);
      case Number number -> out.append(number);
      case Map<?, ?> map -> {
        out.append('{');
        boolean first = true;
        for (Map.Entry<?, ?> entry : map.entrySet()) {
          if (!first) {
            out.append(',');
          }
          first = false;
          writeString(out, String.valueOf(entry.getKey()));
          out.append(':');
          write(out, entry.getValue());
        }
        out.append('}');
      }
      case Iterable<?> items -> {
        out.append('[');
        boolean first = true;
        for (Object item : items) {
          if (!first) {
            out.append(',');
          }
          first = false;
          write(out, item);
        }
        out.append(']');
      }
      default -> writeString(out, String.valueOf(value));
    }
  }

  private static void writeString(StringBuilder out, String text) {
    out.append('"');
    for (int index = 0; index < text.length(); index++) {
      char ch = text.charAt(index);
      switch (ch) {
        case '"' -> out.append("\\\"");
        case '\\' -> out.append("\\\\");
        case '\b' -> out.append("\\b");
        case '\f' -> out.append("\\f");
        case '\n' -> out.append("\\n");
        case '\r' -> out.append("\\r");
        case '\t' -> out.append("\\t");
        default -> {
          if (ch < 0x20) {
            out.append(String.format("\\u%04x", (int) ch));
          } else {
            out.append(ch);
          }
        }
      }
    }
    out.append('"');
  }

  private static final class Parser {
    private final String text;
    private int index;

    private Parser(String text) {
      this.text = text;
    }

    private Object parseValue() {
      skip();
      if (index >= text.length()) {
        throw new IllegalArgumentException("Unexpected end of JSON");
      }
      char ch = text.charAt(index);
      return switch (ch) {
        case '{' -> parseObject();
        case '[' -> parseArray();
        case '"' -> parseString();
        case 't' -> parseLiteral("true", Boolean.TRUE);
        case 'f' -> parseLiteral("false", Boolean.FALSE);
        case 'n' -> parseLiteral("null", null);
        default -> parseNumber();
      };
    }

    private Map<String, Object> parseObject() {
      expect('{');
      Map<String, Object> object = new LinkedHashMap<>();
      skip();
      if (peek('}')) {
        index++;
        return object;
      }
      while (true) {
        skip();
        String key = parseString();
        skip();
        expect(':');
        object.put(key, parseValue());
        skip();
        if (peek('}')) {
          index++;
          return object;
        }
        expect(',');
      }
    }

    private List<Object> parseArray() {
      expect('[');
      List<Object> array = new ArrayList<>();
      skip();
      if (peek(']')) {
        index++;
        return array;
      }
      while (true) {
        array.add(parseValue());
        skip();
        if (peek(']')) {
          index++;
          return array;
        }
        expect(',');
      }
    }

    private String parseString() {
      expect('"');
      StringBuilder out = new StringBuilder();
      while (index < text.length()) {
        char ch = text.charAt(index++);
        if (ch == '"') {
          return out.toString();
        }
        if (ch != '\\') {
          out.append(ch);
          continue;
        }
        if (index >= text.length()) {
          throw new IllegalArgumentException("Unterminated string escape");
        }
        char escaped = text.charAt(index++);
        out.append(
            switch (escaped) {
              case '"' -> '"';
              case '\\' -> '\\';
              case '/' -> '/';
              case 'b' -> '\b';
              case 'f' -> '\f';
              case 'n' -> '\n';
              case 'r' -> '\r';
              case 't' -> '\t';
              case 'u' -> parseUnicode();
              default -> throw new IllegalArgumentException("Invalid escape");
            });
      }
      throw new IllegalArgumentException("Unterminated string");
    }

    private char parseUnicode() {
      if (index + 4 > text.length()) {
        throw new IllegalArgumentException("Invalid unicode escape");
      }
      int code = Integer.parseInt(text.substring(index, index + 4), 16);
      index += 4;
      return (char) code;
    }

    private Object parseLiteral(String literal, Object value) {
      if (!text.startsWith(literal, index)) {
        throw new IllegalArgumentException("Invalid literal");
      }
      index += literal.length();
      return value;
    }

    private Number parseNumber() {
      int start = index;
      if (peek('-')) {
        index++;
      }
      while (index < text.length() && Character.isDigit(text.charAt(index))) {
        index++;
      }
      boolean decimal = false;
      if (peek('.')) {
        decimal = true;
        index++;
        while (index < text.length() && Character.isDigit(text.charAt(index))) {
          index++;
        }
      }
      if (peek('e') || peek('E')) {
        decimal = true;
        index++;
        if (peek('+') || peek('-')) {
          index++;
        }
        while (index < text.length() && Character.isDigit(text.charAt(index))) {
          index++;
        }
      }
      String raw = text.substring(start, index);
      if (decimal) {
        return Double.valueOf(raw);
      }
      long value = Long.parseLong(raw);
      if (value >= Integer.MIN_VALUE && value <= Integer.MAX_VALUE) {
        return (int) value;
      }
      return value;
    }

    private void skip() {
      while (index < text.length() && Character.isWhitespace(text.charAt(index))) {
        index++;
      }
    }

    private boolean peek(char expected) {
      return index < text.length() && text.charAt(index) == expected;
    }

    private void expect(char expected) {
      skip();
      if (!peek(expected)) {
        throw new IllegalArgumentException("Expected " + expected);
      }
      index++;
    }
  }
}
