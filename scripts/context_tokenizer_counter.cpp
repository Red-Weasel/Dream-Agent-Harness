// DREAM-257 phase 1: CPU-only piece-text token counter over the engine's own, unchanged GGUF tokenizer.
//
// What this is: a thin protocol shim. It links the engine's public ie::GgufReader and ie::Tokenizer (the parent
// compiles it with three unchanged engine translation units and a standard compiler; no GPU, no SYCL, no ie_core
// library) and answers "how many tokens is this text" over bounded JSON lines. It contains no tokenizer and no
// chat-template renderer of its own, and it never creates an engine instance.
//
// What a count is: the length of Tokenizer::encode(text, allow_special = false) for ONE piece of text alone. It
// is not a count of a rendered chat: role markers, turn separators, template special tokens, tool and image
// formatting are not in it. Literal text that spells a special token is counted as ordinary text.
//
// What this does not establish: that the GGUF is the checkpoint an engine is serving, or that this build equals a
// running engine binary. It reports facts about the file it was given, and nothing else.
//
// Usage (fixed order, nothing else accepted):
//   context_tokenizer_counter --gguf <path> --session <32 lowercase hex> --open pinned [--diag]
//
// Output discipline: the protocol is written to a private duplicate of stdout; fd 1 itself is pointed at
// /dev/null so that nothing a linked unit prints can enter the protocol. No path, no argv, no request text and no
// library error text is ever written: failures are fixed reason codes.
//
// Not verified by its author: this file was written without access to the engine's sources and was never
// compiled. Accessor return types are adapted with templates (label_of / id_of / flag_of / nonempty); anything
// else that does not match the real headers is a compile gap for the parent to report, not a success.

#include "ie/gguf.hpp"
#include "ie/tokenizer.hpp"

#if __has_include(<nlohmann/json.hpp>)
#include <nlohmann/json.hpp>
#else
#include "json.hpp"
#endif

#include <cerrno>
#include <csignal>
#include <cstdint>
#include <cstring>
#include <exception>
#include <string>
#include <string_view>
#include <type_traits>
#include <vector>

#include <fcntl.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <unistd.h>

namespace {

using json = nlohmann::json;

constexpr const char* kProtocol = "dream-context-counter/1";
constexpr std::size_t kMaxLineBytes = 4u * 1024u * 1024u;      // one request line, newline excluded
constexpr std::size_t kMaxTextBytes = 2u * 1024u * 1024u;      // the decoded text of one count request
constexpr std::size_t kMaxDiagTextBytes = 64u * 1024u;         // --diag only
constexpr std::size_t kMaxDiagIds = 16384;                     // --diag only
constexpr std::uint64_t kMaxHeaderBytes = 512ull * 1024ull * 1024ull;
constexpr std::uint64_t kMaxId = 9007199254740991ull;          // 2^53 - 1
constexpr std::size_t kMaxPathBytes = 4096;

// --- SHA-256 (FIPS 180-4), for the digest of the GGUF header region only -----------------------------------

struct Sha256 {
    std::uint32_t h[8] = {0x6a09e667u, 0xbb67ae85u, 0x3c6ef372u, 0xa54ff53au,
                          0x510e527fu, 0x9b05688cu, 0x1f83d9abu, 0x5be0cd19u};
    unsigned char block[64] = {};
    std::size_t fill = 0;
    std::uint64_t total = 0;

    static std::uint32_t rotr(std::uint32_t x, int n) { return (x >> n) | (x << (32 - n)); }

    void compress(const unsigned char* p) {
        static const std::uint32_t k[64] = {
            0x428a2f98u, 0x71374491u, 0xb5c0fbcfu, 0xe9b5dba5u, 0x3956c25bu, 0x59f111f1u, 0x923f82a4u, 0xab1c5ed5u,
            0xd807aa98u, 0x12835b01u, 0x243185beu, 0x550c7dc3u, 0x72be5d74u, 0x80deb1feu, 0x9bdc06a7u, 0xc19bf174u,
            0xe49b69c1u, 0xefbe4786u, 0x0fc19dc6u, 0x240ca1ccu, 0x2de92c6fu, 0x4a7484aau, 0x5cb0a9dcu, 0x76f988dau,
            0x983e5152u, 0xa831c66du, 0xb00327c8u, 0xbf597fc7u, 0xc6e00bf3u, 0xd5a79147u, 0x06ca6351u, 0x14292967u,
            0x27b70a85u, 0x2e1b2138u, 0x4d2c6dfcu, 0x53380d13u, 0x650a7354u, 0x766a0abbu, 0x81c2c92eu, 0x92722c85u,
            0xa2bfe8a1u, 0xa81a664bu, 0xc24b8b70u, 0xc76c51a3u, 0xd192e819u, 0xd6990624u, 0xf40e3585u, 0x106aa070u,
            0x19a4c116u, 0x1e376c08u, 0x2748774cu, 0x34b0bcb5u, 0x391c0cb3u, 0x4ed8aa4au, 0x5b9cca4fu, 0x682e6ff3u,
            0x748f82eeu, 0x78a5636fu, 0x84c87814u, 0x8cc70208u, 0x90befffau, 0xa4506cebu, 0xbef9a3f7u, 0xc67178f2u};
        std::uint32_t w[64];
        for (int i = 0; i < 16; ++i) {
            w[i] = (std::uint32_t(p[4 * i]) << 24) | (std::uint32_t(p[4 * i + 1]) << 16) |
                   (std::uint32_t(p[4 * i + 2]) << 8) | std::uint32_t(p[4 * i + 3]);
        }
        for (int i = 16; i < 64; ++i) {
            const std::uint32_t s0 = rotr(w[i - 15], 7) ^ rotr(w[i - 15], 18) ^ (w[i - 15] >> 3);
            const std::uint32_t s1 = rotr(w[i - 2], 17) ^ rotr(w[i - 2], 19) ^ (w[i - 2] >> 10);
            w[i] = w[i - 16] + s0 + w[i - 7] + s1;
        }
        std::uint32_t a = h[0], b = h[1], c = h[2], d = h[3], e = h[4], f = h[5], g = h[6], hh = h[7];
        for (int i = 0; i < 64; ++i) {
            const std::uint32_t s1 = rotr(e, 6) ^ rotr(e, 11) ^ rotr(e, 25);
            const std::uint32_t ch = (e & f) ^ (~e & g);
            const std::uint32_t t1 = hh + s1 + ch + k[i] + w[i];
            const std::uint32_t s0 = rotr(a, 2) ^ rotr(a, 13) ^ rotr(a, 22);
            const std::uint32_t maj = (a & b) ^ (a & c) ^ (b & c);
            const std::uint32_t t2 = s0 + maj;
            hh = g; g = f; f = e; e = d + t1; d = c; c = b; b = a; a = t1 + t2;
        }
        h[0] += a; h[1] += b; h[2] += c; h[3] += d; h[4] += e; h[5] += f; h[6] += g; h[7] += hh;
    }

    void update(const unsigned char* data, std::size_t size) {
        total += size;
        while (size > 0) {
            const std::size_t take = (64 - fill < size) ? 64 - fill : size;
            std::memcpy(block + fill, data, take);
            fill += take; data += take; size -= take;
            if (fill == 64) { compress(block); fill = 0; }
        }
    }

    std::string hex() {
        const std::uint64_t bits = total * 8;
        const unsigned char one = 0x80, zero = 0;
        std::uint64_t keep = total;
        update(&one, 1);
        while (fill != 56) update(&zero, 1);
        unsigned char length[8];
        for (int i = 0; i < 8; ++i) length[i] = static_cast<unsigned char>(bits >> (56 - 8 * i));
        update(length, 8);
        total = keep;
        static const char digits[] = "0123456789abcdef";
        std::string out;
        out.reserve(64);
        for (int i = 0; i < 8; ++i) {
            for (int shift = 28; shift >= 0; shift -= 4) out.push_back(digits[(h[i] >> shift) & 0xf]);
        }
        return out;
    }
};

// --- adapters over accessor return types the author could not inspect ---------------------------------------

// A short identity label from metadata: letters, digits, '.', '_' and '-' only, at most 64 of them. Anything else
// becomes '?', so text taken from an untrusted file can never carry a path or break the protocol line.
std::string clean(std::string_view text) {
    std::string out;
    for (const char c : text.substr(0, 64)) {
        const bool ok = (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') ||
                        c == '.' || c == '_' || c == '-';
        out.push_back(ok ? c : '?');
    }
    return out;
}

template <class T>
std::string label_of(const T& value) {
    if constexpr (std::is_convertible_v<const T&, std::string_view>) {
        return clean(std::string_view(value));
    } else if constexpr (std::is_enum_v<T>) {
        return "enum-" + std::to_string(static_cast<long long>(value));
    } else if constexpr (std::is_arithmetic_v<T>) {
        return "num-" + std::to_string(static_cast<long long>(value));
    } else {
        return "unrepresentable";
    }
}

template <class T>
bool nonempty(const T& value) {
    if constexpr (std::is_same_v<T, bool>) {
        return value;
    } else if constexpr (std::is_convertible_v<const T&, std::string_view>) {
        return !std::string_view(value).empty();
    } else if constexpr (requires { value.empty(); }) {
        return !value.empty();
    } else if constexpr (requires { value.has_value(); }) {
        return value.has_value();
    } else {
        return true;       // cannot be read: said to be a warning, never silently "none"
    }
}

template <class T>
json id_of(const T& value) {
    if constexpr (std::is_arithmetic_v<T>) {
        return json(static_cast<long long>(value));
    } else if constexpr (requires { value.has_value(); *value; }) {
        if (value.has_value()) return json(static_cast<long long>(*value));
        return json(nullptr);
    } else {
        return json(nullptr);
    }
}

// 1 = true, 0 = false, -1 = not stated / not readable.
template <class T>
int flag_of(const T& value) {
    if constexpr (std::is_same_v<T, bool>) {
        return value ? 1 : 0;
    } else if constexpr (requires { value.has_value(); *value; }) {
        return value.has_value() ? ((*value) ? 1 : 0) : -1;
    } else if constexpr (std::is_arithmetic_v<T>) {
        return value != 0 ? 1 : 0;
    } else {
        return -1;
    }
}

// --- protocol output ----------------------------------------------------------------------------------------

int g_out = -1;
std::string g_session;       // empty until argv has been validated

void put(const json& message) {
    std::string line = message.dump(-1, ' ', true, json::error_handler_t::replace);
    line.push_back('\n');
    std::size_t at = 0;
    while (at < line.size()) {
        const ssize_t n = ::write(g_out, line.data() + at, line.size() - at);
        if (n < 0) {
            if (errno == EINTR) continue;
            ::_exit(3);                      // the reader is gone: nothing left to say or to whom
        }
        at += static_cast<std::size_t>(n);
    }
}

[[noreturn]] void fatal(const char* reason) {
    if (g_out >= 0) {
        put(json{{"v", 1}, {"type", "fatal"}, {"session", g_session}, {"reason", reason}});
    }
    ::_exit(2);
}

// --- bounded line input -------------------------------------------------------------------------------------

// Reads fd 0 in fixed chunks. The buffer never holds more than kMaxLineBytes + one chunk: a line longer than the
// limit is refused as soon as that many bytes have arrived without a newline, however long the sender goes on.
class Lines {
public:
    enum Result { kLine, kEnd, kTooLong, kBroken };

    Result next(std::string& line) {
        for (;;) {
            const std::size_t at = buffer_.find('\n', scanned_);
            if (at != std::string::npos) {
                if (at > kMaxLineBytes) return kTooLong;
                line.assign(buffer_, 0, at);
                buffer_.erase(0, at + 1);
                scanned_ = 0;
                return kLine;
            }
            scanned_ = buffer_.size();
            if (buffer_.size() > kMaxLineBytes) return kTooLong;
            if (ended_) return buffer_.empty() ? kEnd : kBroken;      // a last line without its newline
            char chunk[65536];
            const ssize_t n = ::read(STDIN_FILENO, chunk, sizeof chunk);
            if (n < 0) {
                if (errno == EINTR) continue;
                return kBroken;
            }
            if (n == 0) {
                ended_ = true;
                continue;
            }
            buffer_.append(chunk, static_cast<std::size_t>(n));
        }
    }

private:
    std::string buffer_;
    std::size_t scanned_ = 0;
    bool ended_ = false;
};

// True when the line's JSON nests no deeper than `most` (strings skipped). Checked before the parser sees it.
bool shallow(const std::string& line, int most) {
    int depth = 0;
    bool in_string = false, escaped = false;
    for (const char c : line) {
        if (in_string) {
            if (escaped) escaped = false;
            else if (c == '\\') escaped = true;
            else if (c == '"') in_string = false;
            continue;
        }
        if (c == '"') in_string = true;
        else if (c == '{' || c == '[') { if (++depth > most) return false; }
        else if (c == '}' || c == ']') --depth;
    }
    return true;
}

bool is_session(std::string_view text) {
    if (text.size() != 32) return false;
    for (const char c : text) {
        if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    }
    return true;
}

long long nanoseconds(const struct timespec& t) {
    return static_cast<long long>(t.tv_sec) * 1000000000LL + static_cast<long long>(t.tv_nsec);
}

bool same_file(const struct stat& a, const struct stat& b) {
    return a.st_dev == b.st_dev && a.st_ino == b.st_ino && a.st_size == b.st_size &&
           nanoseconds(a.st_mtim) == nanoseconds(b.st_mtim) && nanoseconds(a.st_ctim) == nanoseconds(b.st_ctim);
}

int run(int argc, char** argv) {
    // argv: fixed order, fixed count. Its values are never written anywhere.
    if (argc != 7 && argc != 8) fatal("usage");
    if (std::strcmp(argv[1], "--gguf") != 0 || std::strcmp(argv[3], "--session") != 0 ||
        std::strcmp(argv[5], "--open") != 0) fatal("usage");
    const bool diag = argc == 8;
    if (diag && std::strcmp(argv[7], "--diag") != 0) fatal("usage");
    const std::string path = argv[2];
    const std::string open_mode = argv[6];
    if (path.empty() || path.size() > kMaxPathBytes || !is_session(argv[4]) ||
        open_mode != "pinned") fatal("usage");
    g_session = argv[4];

    // ONE descriptor on the file, opened without blocking; a FIFO or a device is refused before anything reads it.
    const int fd = ::open(path.c_str(), O_RDONLY | O_NONBLOCK | O_CLOEXEC | O_NOCTTY);
    if (fd < 0) fatal("gguf_open_failed");
    struct stat before {};
    if (::fstat(fd, &before) != 0 || !S_ISREG(before.st_mode)) fatal("gguf_open_failed");

    // "pinned": the reader is given the descriptor's own /proc name, so it maps the very file that was checked and
    // that the digest below is read from. Sibling files of a split GGUF cannot be found that way.
    // Path-based reopening permits an A/B/A pathname race; it is deliberately unsupported.
    const std::string reader_path = "/proc/self/fd/" + std::to_string(fd);
    ie::GgufReader reader;
    {
        const std::string error = reader.open(reader_path);      // its text may name a path: never forwarded
        if (!error.empty() || !reader.valid()) fatal("gguf_open_failed");
    }

    const std::uint64_t header_bytes = static_cast<std::uint64_t>(reader.tensor_data_offset());
    if (header_bytes == 0 || header_bytes > kMaxHeaderBytes ||
        header_bytes > static_cast<std::uint64_t>(before.st_size)) fatal("gguf_header_unusable");

    // The digest: bytes [0, tensor_data_offset) (header, metadata, tensor descriptors), read through the pinned
    // descriptor. No tensor payload is read here.
    Sha256 digest;
    {
        std::vector<unsigned char> chunk(1u << 20);
        std::uint64_t at = 0;
        while (at < header_bytes) {
            const std::uint64_t want64 = header_bytes - at < chunk.size() ? header_bytes - at : chunk.size();
            const ssize_t n = ::pread(fd, chunk.data(), static_cast<std::size_t>(want64), static_cast<off_t>(at));
            if (n < 0) {
                if (errno == EINTR) continue;
                fatal("gguf_open_failed");
            }
            if (n == 0) fatal("gguf_changed");
            digest.update(chunk.data(), static_cast<std::size_t>(n));
            at += static_cast<std::uint64_t>(n);
        }
    }
    const std::string metadata_sha256 = digest.hex();

    ie::Tokenizer tokenizer;
    {
        const std::string error = tokenizer.load_from_gguf(reader);
        if (!error.empty() || !tokenizer.ready()) fatal("tokenizer_load_failed");
    }

    // BOS: encode() adds one by itself when the metadata says so, whatever allow_special is. Nothing is ever
    // subtracted to "correct" that. Counting is offered only when the tokenizer states add_bos = false AND an
    // empty text really encodes to nothing; otherwise every count request is refused with the reason.
    const int add_bos = flag_of(tokenizer.add_bos_token());
    const std::size_t empty_tokens = tokenizer.encode(std::string_view{}, false).size();
    const bool counting = add_bos == 0 && empty_tokens == 0;
    const char* refusal = add_bos != 0 ? "bos_auto_add_unsupported" : "empty_text_not_zero";

    struct stat after {};
    if (::fstat(fd, &after) != 0 || !same_file(before, after)) fatal("gguf_changed");

    json ready = {
        {"v", 1}, {"type", "ready"}, {"protocol", kProtocol}, {"session", g_session},
        {"counting", counting}, {"diag", diag},
        {"tokenizer", {
            {"vocab_size", static_cast<unsigned long long>(tokenizer.vocab_size())},
            {"pre", label_of(tokenizer.pre())},
            {"pre_warning", nonempty(tokenizer.pre_warning())},
            {"bos_token_id", id_of(tokenizer.bos_token_id())},
            {"eos_token_id", id_of(tokenizer.eos_token_id())},
            {"pad_token_id", id_of(tokenizer.pad_token_id())},
            {"add_bos", add_bos < 0 ? json(nullptr) : json(add_bos == 1)},
            {"allow_special", false},
            {"empty_text_tokens", static_cast<unsigned long long>(empty_tokens)}}},
        {"gguf", {
            {"version", static_cast<unsigned long long>(reader.version())},
            {"tensors", static_cast<unsigned long long>(reader.n_tensors())},
            {"file_size", static_cast<unsigned long long>(before.st_size)},
            {"reader_file_size", static_cast<unsigned long long>(reader.file_size())},
            {"header_bytes", static_cast<unsigned long long>(header_bytes)},
            {"metadata_sha256", metadata_sha256},
            {"open", open_mode},
            {"dev", static_cast<unsigned long long>(before.st_dev)},
            {"ino", static_cast<unsigned long long>(before.st_ino)},
            {"mtime_ns", nanoseconds(before.st_mtim)}}},
        {"limits", {{"line_bytes", static_cast<unsigned long long>(kMaxLineBytes)},
                    {"text_bytes", static_cast<unsigned long long>(kMaxTextBytes)}}}};
    if (!counting) ready["reason"] = refusal;
    put(ready);

    Lines lines;
    std::string line;
    for (;;) {
        const Lines::Result got = lines.next(line);
        if (got == Lines::kEnd) return 0;
        if (got == Lines::kTooLong) fatal("line_too_long");
        if (got == Lines::kBroken) fatal("input_broken");

        // One object per line, nothing before or after it, exactly the four known keys, each once.
        if (line.size() < 2 || line.front() != '{' || line.back() != '}' || !shallow(line, 1)) {
            fatal("request_malformed");
        }
        int keys = 0;
        const json::parser_callback_t count_keys = [&keys](int, json::parse_event_t event, json&) {
            if (event == json::parse_event_t::key) ++keys;
            return true;
        };
        const json request = json::parse(line.begin(), line.end(), count_keys, false);
        if (request.is_discarded() || !request.is_object() || keys != 4 || request.size() != 4) {
            fatal("request_malformed");
        }
        const auto v = request.find("v"), op = request.find("op"), id = request.find("id"),
                   text = request.find("text");
        if (v == request.end() || op == request.end() || id == request.end() || text == request.end() ||
            !v->is_number_unsigned() || v->get<std::uint64_t>() != 1 || !op->is_string() ||
            !id->is_number_unsigned() || id->get<std::uint64_t>() > kMaxId || !text->is_string()) {
            fatal("request_malformed");
        }
        const std::uint64_t request_id = id->get<std::uint64_t>();
        const std::string& operation = op->get_ref<const std::string&>();
        // The parser has already required valid UTF-8 and decoded the escapes: these are the caller's bytes,
        // newlines, NULs and a literal backslash-n included. They are handed to encode() as they are.
        const std::string& body = text->get_ref<const std::string&>();

        if (operation == "count") {
            json answer = {{"v", 1}, {"type", "count"}, {"id", request_id}, {"session", g_session}, {"ok", false}};
            if (!counting) {
                answer["reason"] = refusal;
            } else if (body.size() > kMaxTextBytes) {
                answer["reason"] = "text_too_large";
            } else {
                bool done = false;
                std::size_t tokens = 0;
                try {
                    tokens = tokenizer.encode(std::string_view(body), false).size();
                    done = true;
                } catch (const std::exception&) {
                } catch (...) {
                }
                if (done) {
                    answer["ok"] = true;
                    answer["count"] = static_cast<unsigned long long>(tokens);
                } else {
                    answer["reason"] = "encode_failed";
                }
            }
            put(answer);
        } else if (operation == "encode" && diag) {
            // Parity fixtures only: token ids for a small text, only when --diag was given on the command line.
            json answer = {{"v", 1}, {"type", "encode"}, {"id", request_id}, {"session", g_session}, {"ok", false}};
            if (body.size() > kMaxDiagTextBytes) {
                answer["reason"] = "diag_too_large";
            } else {
                try {
                    const auto ids = tokenizer.encode(std::string_view(body), false);
                    if (ids.size() > kMaxDiagIds) {
                        answer["reason"] = "diag_too_large";
                    } else {
                        json list = json::array();
                        for (const auto token : ids) list.push_back(static_cast<long long>(token));
                        answer["ok"] = true;
                        answer["count"] = static_cast<unsigned long long>(ids.size());
                        answer["ids"] = std::move(list);
                    }
                } catch (const std::exception&) {
                    answer["reason"] = "encode_failed";
                } catch (...) {
                    answer["reason"] = "encode_failed";
                }
            }
            put(answer);
        } else {
            fatal("request_malformed");
        }
    }
}

}  // namespace

int main(int argc, char** argv) {
    ::signal(SIGPIPE, SIG_IGN);
    // The protocol goes to a private duplicate of stdout; fd 1 is pointed at /dev/null.
    g_out = ::fcntl(STDOUT_FILENO, F_DUPFD_CLOEXEC, 3);
    if (g_out < 0) return 3;
    const int sink = ::open("/dev/null", O_WRONLY | O_CLOEXEC);
    if (sink < 0 || ::dup2(sink, STDOUT_FILENO) < 0) return 3;
    try {
        return run(argc, argv);
    } catch (const std::exception&) {
        fatal("internal_error");
    } catch (...) {
        fatal("internal_error");
    }
}
