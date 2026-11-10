#include "4DPluginAPI.h"
#include "PrivateTypes.h"
#include "EntryPoints.h"

#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <map>
#include <new>
#include <stdexcept>
#include <string>

static void require(bool condition, const char *message)
{
    if (!condition)
    {
        std::fprintf(stderr, "FAIL: %s\n", message);
        std::exit(1);
    }
}

static std::map<PA_Unichar *, size_t> strings;
static std::map<PA_Handle, size_t> handles;
struct TrackedPicture { unsigned int references; };
static std::map<PA_Picture, TrackedPicture *> pictures;
static unsigned int invalidDisposals;
static bool nullEmpty;
static bool failHandle;
static bool failCommand;
static bool replacePicture = true;
static CUTF16String expectedText;
static const char *scenario;

static CUTF16String utf16(const char16_t *value)
{
    CUTF16String result;
    while (*value) result.push_back(static_cast<PA_Unichar>(*value++));
    return result;
}

static PA_Unistring makeString(const PA_Unichar *text)
{
    size_t length = 0;
    if (text) while (text[length]) ++length;
    PA_Unistring result = {};
    if (!length && nullEmpty) return result;
    result.fString = static_cast<PA_Unichar *>(std::malloc((length + 1) * sizeof(PA_Unichar)));
    require(result.fString != nullptr, "fixture string allocation");
    if (length) std::memcpy(result.fString, text, length * sizeof(PA_Unichar));
    result.fString[length] = 0;
    result.fLength = static_cast<PA_long32>(length);
    PA_Unichar *allocation = result.fString;
    strings[allocation] = length;
    return result;
}

static void disposeString(PA_Unistring value)
{
    if (!value.fString) return;
    PA_Unichar *allocation = value.fString;
    auto found = strings.find(allocation);
    if (found == strings.end())
    {
        ++invalidDisposals;
        std::fprintf(stderr, "ownership error: duplicate string disposal\n");
        return;
    }
    strings.erase(found);
    std::free(value.fString);
}

static void checkString(PA_Unistring value, const CUTF16String &expected)
{
    PA_Unichar *allocation = value.fString;
    require(value.fLength == expected.size(), "exact UTF-16 code-unit count");
    if (value.fString)
    {
        require(strings.count(allocation) == 1, "string remains owned and live");
        require(strings.at(allocation) == expected.size(), "exact string allocation length");
        require(value.fString[value.fLength] == 0, "string terminator");
        require(CUTF16String(value.fString, value.fLength) == expected, "exact UTF-16 contents");
    }
    else require(expected.empty(), "null only represents empty");
}

static PA_Picture makePicture()
{
    TrackedPicture *picture = new TrackedPicture{1};
    pictures[picture] = picture;
    return picture;
}

static void checkPicture(PA_Picture value)
{
    require(value && pictures.count(value) == 1, "picture remains live");
    require(pictures.at(value)->references > 0, "positive picture reference count");
}

static void disposePicture(PA_Picture value)
{
    if (!value) return;
    checkPicture(value);
    TrackedPicture *picture = pictures.at(value);
    if (!--picture->references)
    {
        pictures.erase(value);
        delete picture;
    }
}

static void clearVariable(PA_Variable *value)
{
    require(value->fFiller == 0, "fixture clears values, not reference metadata");
    switch (value->fType)
    {
        case eVK_Unistring: disposeString(value->uValue.fString); break;
        case eVK_Picture: disposePicture(value->uValue.fPicture); break;
        case eVK_ArrayUnicode:
        {
            PA_Handle h = value->uValue.fArray.fData;
            if (h)
            {
                require(handles.count(h) == 1, "array handle live");
                require(handles.at(h) == (value->uValue.fArray.fNbElements + 1) * sizeof(PA_Unistring),
                        "array storage matches logical count (not resize-OOM certification)");
                PA_Unistring *items = reinterpret_cast<PA_Unistring *>(*h);
                for (PA_long32 i = 0; i <= value->uValue.fArray.fNbElements; ++i)
                    disposeString(items[i]);
                PA_DisposeHandle(h);
            }
            break;
        }
        case eVK_Undefined:
        case eVK_Longint: break;
        default: require(false, "unsupported clear kind, including raw-kind-zero sentinel");
    }
    *value = {};
    value->fType = eVK_Undefined;
}

static void command(EngineBlock *eb)
{
    PA_Variable **args = static_cast<PA_Variable **>(eb->fPtr1);
    PA_Variable *returned = static_cast<PA_Variable *>(eb->fPtr2);
    require(returned->fType == eVK_Undefined, "command return initialized by actual SDK");
    if (failCommand) { eb->fError = eER_IncompatibleTypes; return; }
    switch (eb->fParam1)
    {
        case 678:
            require(eb->fParam2 == 2, "READ PICTURE FILE arity");
            checkString(args[0]->uValue.fString, expectedText);
            clearVariable(args[1]);
            PA_SetPictureVariable(args[1], makePicture());
            break;
        case 1002:
            require(eb->fParam2 == 2, "CONVERT PICTURE arity");
            checkString(args[1]->uValue.fString, expectedText);
            checkPicture(args[0]->uValue.fPicture);
            if (replacePicture)
            {
                clearVariable(args[0]);
                PA_SetPictureVariable(args[0], makePicture());
            }
            break;
        case 988:
            require(eb->fParam2 == 2 && PA_GetLongintVariable(*args[1]) == 101,
                    "TRANSFORM PICTURE grayscale operation");
            checkPicture(args[0]->uValue.fPicture);
            if (replacePicture)
            {
                clearVariable(args[0]);
                PA_SetPictureVariable(args[0], makePicture());
            }
            break;
        case 457:
            require(eb->fParam2 == 3, "PICTURE PROPERTIES arity");
            checkPicture(args[0]->uValue.fPicture);
            PA_SetLongintVariable(args[1], 17);
            PA_SetLongintVariable(args[2], 23);
            break;
        case 679:
            require(eb->fParam2 == 4 && PA_GetLongintVariable(*args[2]) == 7 &&
                    PA_GetLongintVariable(*args[3]) == 9, "CREATE THUMBNAIL inputs");
            checkPicture(args[0]->uValue.fPicture);
            clearVariable(args[1]);
            PA_SetPictureVariable(args[1], makePicture());
            break;
        default: require(false, "unexpected host command");
    }
}

static void engine(short selector, EngineBlock *eb)
{
    switch (selector)
    {
        case EX_CREATE_UNISTRING:
            eb->fUniString1 = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            break;
        case EX_DISPOSE_UNISTRING: disposeString(eb->fUniString1); break;
        case EX_SET_UNISTRING:
        {
            PA_Unistring copy = makeString(reinterpret_cast<PA_Unichar *>(eb->fHandle));
            disposeString(eb->fUniString1);
            eb->fUniString1 = copy;
            break;
        }
        case EX_CREATE_PICTURE: eb->fPicture = makePicture(); eb->fError = 0; break;
        case EX_DISPOSE_PICTURE: disposePicture(eb->fPicture); break;
        case EX_DUPLICATE_PICTURE:
        {
            PA_Picture picture = eb->fPicture;
            if (eb->fPicture)
            {
                checkPicture(eb->fPicture);
                if (!eb->fParam1) ++pictures.at(picture)->references;
                else eb->fPicture = makePicture();
            }
            break;
        }
        case EX_GET_PICTURE_DATA:
            // Metadata output ownership is deliberately not assumed. Failure
            // leaves the descriptor poisoned; production must check the error.
            std::memset(&eb->fUniString1, 0xA5, sizeof eb->fUniString1);
            eb->fError = eER_IncompatibleTypes;
            break;
        case EX_CLEAR_VARIABLE: clearVariable(static_cast<PA_Variable *>(eb->fPtr1)); break;
        case EX_EXECUTE_COMMAND_BY_ID: command(eb); break;
        case EX_CALL_BY_PROCID:
        {
            require(eb->fParam1 == 1 && eb->fParam2 == 1, "synthetic synchronous callback");
            PA_Variable *values = reinterpret_cast<PA_Variable *>(*eb->fHandle);
            require(values[0].fType == eVK_Undefined, "method return starts Undefined");
            checkString(values[1].uValue.fString, expectedText);
            PA_Unistring result = makeString(expectedText.c_str());
            PA_SetStringVariable(&values[0], &result);
            eb->fError = 0;
            break;
        }
        case EX_HANDLE_MANAGER:
        {
            PA_Handle handle = nullptr;
            if (eb->fParam1 != 1) handle = eb->fHandle;
            eb->fError = 0;
            switch (eb->fParam1)
            {
                case 1:
                    if (failHandle)
                    {
                        eb->fHandle = nullptr;
                        eb->fError = eER_IncompatibleTypes;
                        break;
                    }
                    handle = static_cast<PA_Handle>(std::malloc(sizeof(char *)));
                    require(handle != nullptr, "fixture handle allocation");
                    *handle = static_cast<char *>(std::calloc(1, eb->fParam2 ? eb->fParam2 : 1));
                    require(*handle != nullptr, "fixture handle data allocation");
                    handles[handle] = eb->fParam2;
                    eb->fHandle = handle;
                    break;
                case 2:
                    require(handles.erase(handle) == 1, "handle disposed once");
                    std::free(*eb->fHandle);
                    std::free(eb->fHandle);
                    break;
                case 3:
                    require(handles.count(handle) == 1, "resize live handle");
                    *eb->fHandle = static_cast<char *>(std::realloc(*eb->fHandle, eb->fParam2 ? eb->fParam2 : 1));
                    require(*eb->fHandle != nullptr, "fixture realloc");
                    handles[handle] = eb->fParam2;
                    break;
                case 4: eb->fParam2 = handles.at(handle); break;
                case 5:
                    eb->fParam3 = handle ? reinterpret_cast<sLONG_PTR>(*handle) : 0;
                    break;
                case 6: break;
                default: require(false, "unexpected handle operation");
            }
            break;
        }
        case EX_CONVERT_CHARSET_TO_CHARSET:
        {
            require(eb->fParam2 == eVTC_UTF_8 && eb->fParam4 == eVTC_UTF_16,
                    "charset conversion direction");
            CFStringRef text = CFStringCreateWithBytes(kCFAllocatorDefault,
                static_cast<const UInt8 *>(eb->fPtr1), eb->fParam1, kCFStringEncodingUTF8, false);
            require(text != nullptr, "valid synthetic UTF-8");
            CFIndex units = CFStringGetLength(text);
            require(eb->fParam3 == (eb->fParam1 + 1) * sizeof(PA_Unichar),
                    "production conversion capacity includes room for a UTF-16 terminator");
            require(units * sizeof(PA_Unichar) <= eb->fParam3, "capacity is bytes");
            CFStringGetCharacters(text, CFRangeMake(0, units), static_cast<UniChar *>(eb->fPtr2));
            // The SDK forwards a byte capacity and returns bytes used, not units.
            // No undocumented terminator is written beyond those bytes.
            eb->fParam3 = units * sizeof(PA_Unichar);
            eb->fError = 0;
            CFRelease(text);
            break;
        }
        default:
            std::fprintf(stderr, "unexpected engine selector: %d\n", selector);
            require(false, "unexpected engine operation");
    }
}

static void balance()
{
    if (!strings.empty() || !handles.empty() || !pictures.empty() || invalidDisposals)
    {
        unsigned int references = 0;
        for (const auto &entry : pictures) references += entry.second->references;
        std::fprintf(stderr, "%s: strings=%zu handles=%zu pictures=%zu refs=%u invalid-disposals=%u\n",
                     scenario, strings.size(), handles.size(), pictures.size(), references, invalidDisposals);
        require(false, "explicit ownership balances");
    }
}

static void pictureCase(const std::string &name)
{
    PA_Picture handoff = nullptr;
    {
        CPicture value;
        if (name == "path")
        {
            const char *paths[] = {"test.png", "\xE6\x97\xA5.png", "\xF0\x9F\x99\x82.png", ""};
            const char16_t *texts[] = {u"test.png", u"\u65E5.png", u"\U0001F642.png", u""};
            for (size_t i = 0; i < 4; ++i)
            {
                expectedText = utf16(texts[i]);
                CUTF8String path(reinterpret_cast<const uint8_t *>(paths[i]));
                value.fromFileAtPath(&path);
                checkPicture(value.getPicture());
            }
        }
        else if (name == "bytes")
        {
            const uint8_t bytes[] = {1, 2, 3};
            for (int i = 0; i < 8; ++i) value.setBytes(bytes, sizeof bytes);
            value.setBytes(nullptr, 0);
            checkPicture(value.getPicture());
        }
        else if (name == "copy-units" || name == "copy-owners")
        {
            const char *formats[] = {".png", "\xE6\x97\xA5", "\xF0\x9F\x99\x82", ""};
            const char16_t *texts[] = {u".png", u"\u65E5", u"\U0001F642", u""};
            for (size_t i = name == "copy-owners" ? 3 : 0; i < 4; ++i)
            {
                expectedText = utf16(texts[i]);
                PA_Picture result = value.createCopy(formats[i]);
                checkPicture(result);
                PA_DisposePicture(result);
                checkPicture(value.getPicture());
            }
            expectedText.clear();
            handoff = value.createCopy("");
        }
        else if (name == "gray") handoff = value.createGrayScale();
        else if (name == "size")
        {
            unsigned int width = 0, height = 0;
            value.getSize(&width, &height);
            require(width == (failCommand ? 0 : 17) && height == (failCommand ? 0 : 23),
                    "size output values");
        }
        else if (name == "thumbnail") handoff = value.createThumbnail(7, 9);
        else require(false, "unknown picture scenario");
        checkPicture(value.getPicture());
        if (handoff) checkPicture(handoff);
    }
    if (handoff)
    {
        checkPicture(handoff);
        PA_DisposePicture(handoff);
    }
    balance();
}

static void arrayCase()
{
    const CUTF16String fixtures[] = {utf16(u""), utf16(u"ASCII"), utf16(u"\u65E5"),
                                    utf16(u"\U0001F642"), utf16(u"ASCII")};
    PA_Variable array = PA_CreateVariable(eVK_Undefined);
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&array)};
    {
        ARRAY_TEXT source;
        for (const auto &text : fixtures)
            source.appendUTF16String(text.c_str(), static_cast<uint32_t>(text.size()));
        source.toParamAtIndex(parameters, 1);
    }
    for (PA_long32 i = 0; i < 5; ++i) checkString(PA_GetStringInArray(array, i), fixtures[i]);
    require(PA_GetStringInArray(array, 1).fString != PA_GetStringInArray(array, 4).fString,
            "equal text outputs use independent allocations");
    ARRAY_TEXT copy;
    copy.fromParamAtIndex(parameters, 1);
    PA_Unistring replacement = PA_CreateUnistring(const_cast<PA_Unichar *>(fixtures[2].c_str()));
    PA_SetStringInArray(array, 1, &replacement);
    PA_ResizeArray(&array, 0);
    PA_ClearVariable(&array);
    for (uint32_t i = 0; i < 5; ++i)
    {
        CUTF16String text;
        copy.copyUTF16StringAtIndex(&text, i);
        require(text == fixtures[i], "borrowed text independently copied before replacement/shrink/clear");
    }
    copy.toParamAtIndex(parameters, 1);
    copy.toParamAtIndex(parameters, 1);
    PA_ClearVariable(&array);

    PA_Variable wrong = PA_CreateVariable(eVK_Longint);
    BytePtr wrongParameters[] = {reinterpret_cast<BytePtr>(&wrong)};
    copy.toParamAtIndex(wrongParameters, 1);
    require(wrong.fType == eVK_Longint, "wrapper rejects non-text destinations before allocation");
    PA_ClearVariable(&wrong);

    // Each SDK rejection leaves the supplied owner live and caller-cleanable.
    for (int rejection = 0; rejection < 5; ++rejection)
    {
        PA_Variable target = PA_CreateVariable(eVK_ArrayUnicode);
        PA_Unistring text = PA_CreateUnistring(const_cast<PA_Unichar *>(fixtures[1].c_str()));
        PA_Variable destination = target;
        PA_long32 index = 0;
        if (rejection == 0) destination.fType = eVK_Longint;
        if (rejection == 1) destination.uValue.fArray.fData = nullptr;
        if (rejection == 2) index = -1;
        if (rejection == 3) index = 1;
        PA_SetStringInArray(destination, index, rejection == 4 ? nullptr : &text);
        checkString(text, fixtures[1]);
        PA_DisposeUnistring(&text);
        PA_ClearVariable(&target);
    }
    balance();
}

static void arrayRejectionCase()
{
    ARRAY_TEXT source;
    CUTF16String text = utf16(u"rejected \u65E5 \U0001F642");
    source.appendUTF16String(text.c_str(), static_cast<uint32_t>(text.size()));
    PA_Variable array = {};
    array.fType = eVK_ArrayUnicode;
    PA_Variable original;
    std::memcpy(&original, &array, sizeof array);
    BytePtr parameters[] = {reinterpret_cast<BytePtr>(&array)};
    bool rejected = false;
    try { source.toParamAtIndex(parameters, 1); }
    catch (const std::invalid_argument &) { rejected = true; }
    require(std::memcmp(&array, &original, sizeof array) == 0,
            "null-data rejection preserves destination without creating owners");
    balance();
    require(rejected, "explicit null-data rejection exception");
    array = PA_CreateVariable(eVK_Undefined);
    std::memset(&array.uValue, 0xA5, sizeof array.uValue);
    std::memcpy(&original, &array, sizeof array);
    failHandle = true;
    rejected = false;
    try { source.toParamAtIndex(parameters, 1); }
    catch (const std::bad_alloc &) { rejected = true; }
    failHandle = false;
    require(rejected, "explicit initial allocation failure exception");
    require(std::memcmp(&array, &original, sizeof array) == 0,
            "initial allocation failure preserves every byte of the caller parameter");
    balance();
}

static void scalarCase()
{
    for (const char16_t *fixture : {u"", u"ASCII", u"\u65E5", u"\U0001F642"})
    {
        expectedText = utf16(fixture);
        PA_Unistring input = PA_CreateUnistring(const_cast<PA_Unichar *>(expectedText.c_str()));
        C_TEXT copied;
        BytePtr parameters[] = {reinterpret_cast<BytePtr>(&input)};
        copied.fromParamAtIndex(parameters, 1);
        PA_DisposeUnistring(&input);
        PA_Unistring output = {};
        sLONG_PTR result = reinterpret_cast<sLONG_PTR>(&output);
        copied.setReturn(&result);
        checkString(output, expectedText);
        copied.toParamAtIndex(parameters, 1);
        checkString(input, expectedText);
        PA_DisposeUnistring(&input);
        PA_DisposeUnistring(&output);

        PA_Unistring owned = PA_CreateUnistring(const_cast<PA_Unichar *>(expectedText.c_str()));
        PA_Variable arg = PA_CreateVariable(eVK_Undefined);
        PA_SetStringVariable(&arg, &owned);
        require(arg.uValue.fString.fString == owned.fString, "scalar setter shallow alias");
        PA_Variable returned = PA_ExecuteMethodByID(1, &arg, 1);
        checkString(returned.uValue.fString, expectedText);
        if (!expectedText.empty() || !nullEmpty)
            require(returned.uValue.fString.fString != owned.fString,
                    "callback owning return independent of argument");
        PA_ClearVariable(&returned);
        checkString(arg.uValue.fString, expectedText);
        PA_ClearVariable(&arg);
    }
    balance();
}

static void sentinelCase()
{
    failHandle = true;
    PA_Variable returned = PA_ExecuteCommandByID(678, nullptr, 0);
    require(returned.fType == 0 && returned.fFiller == 0, "command handle failure raw-kind-zero sentinel");
    returned = PA_ExecuteMethodByID(1, nullptr, 0);
    require(returned.fType == eVK_Undefined, "method handle failure Undefined");
    failHandle = false;
    balance();
}

static void dispatcherCase()
{
    PackInitBlock init = {};
    init.fCall4Dex = engine;
    FourDPackex(kInitPlugin, &init, nullptr, nullptr);
    require(init.fSupportedVersion == 0x00001400 && gCall4D == engine, "actual SDK exported initialization");
    for (PA_long32 mode : {0, 1, 7, -1})
    {
        BytePtr parameters[] = {reinterpret_cast<BytePtr>(&mode)};
        sLONG_PTR result = 0;
        FourDPackex(1, parameters, nullptr, &result);
        require(*reinterpret_cast<PA_long32 *>(&result) == -1, "macOS dispatcher unsupported sentinel");
    }
    balance();
}

int main(int argc, char **argv)
{
    require(argc >= 2, "named scenario required");
    scenario = argv[1];
    gCall4D = engine;
    nullEmpty = argc > 2 && std::string(argv[2]) == "null-empty";
    failCommand = argc > 3 && std::string(argv[3]) == "failure";
    replacePicture = !(argc > 3 && std::string(argv[3]) == "unchanged");
    std::string name(scenario);
    if (name == "array") arrayCase();
    else if (name == "array-rejection") arrayRejectionCase();
    else if (name == "scalar") scalarCase();
    else if (name == "sentinel") sentinelCase();
    else if (name == "dispatcher") dispatcherCase();
    else pictureCase(name);
    std::printf("PASS %s (%s, %s): strings=0 handles=0 picture-refs=0\n", scenario,
                nullEmpty ? "null-empty" : "allocated-empty",
                failCommand ? "command failure" : replacePicture ? "replacement" : "unchanged");
}
