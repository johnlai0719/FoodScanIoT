import { useEffect, useMemo, useRef, useState } from 'react';
import { Animated, Image, Linking, Modal, Platform, Pressable, StyleSheet, Text, TextInput, View } from 'react-native';
import type { ImageSourcePropType, ViewInstance } from 'react-native';
import { Gesture, GestureDetector } from 'react-native-gesture-handler';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { Cat } from 'lucide-react-native';
import { sendAssistantQuestion, type AdditiveCitation, type AssistantAnswer } from '../utils/assistantQuestionClient';

// Metro supplies the local artwork when it is available.
const localAsset = require('foodscan-elthen-cat');
const catSheet: ImageSourcePropType | null =
  typeof localAsset === 'number' || typeof localAsset?.uri === 'string' ? localAsset : null;
const frameSize = 64;
const catSize = 52;
const edge = 8;
type Point = { x: number; y: number };
type Bounds = { width: number; height: number };
export type CatDestination = Point & { request: number };

function keepOnScreen(point: Point, { width, height }: Bounds) {
  return {
    x: Math.max(edge, Math.min(point.x, width - catSize - edge)),
    y: Math.max(edge, Math.min(point.y, height - catSize - edge)),
  };
}

const poses = {
  idle: { row: 0, count: 4, interval: 240 },
  run: { row: 5, count: 8, interval: 85 },
  paw: { row: 7, count: 6, interval: 115 },
};

function CatSprite({ pose }: { pose: keyof typeof poses }) {
  const [frame, setFrame] = useState(0);
  const { row, count, interval } = poses[pose];

  useEffect(() => {
    if (catSheet === null) return;
    const timer = setInterval(() => {
      setFrame(current => pose === 'paw' ? Math.min(current + 1, count - 1) : (current + 1) % count);
    }, interval);
    return () => clearInterval(timer);
  }, [count, interval, pose]);

  if (catSheet === null) return <Cat size={26} color="#145c50" />;
  return (
    <View style={styles.frame} accessible={false}>
      <Image
        source={catSheet}
        fadeDuration={0}
        style={[styles.sheet, { left: -frame * frameSize, top: -row * frameSize }]}
      />
    </View>
  );
}

export default function NavigationCat({ destination, onNavigate, onArrive, onOpenAdditive }: {
  destination: CatDestination | null;
  onNavigate: (text: string) => string | undefined;
  onArrive: (request: number) => void;
  onOpenAdditive: (citation: AdditiveCitation) => boolean;
}) {
  const insets = useSafeAreaInsets();
  const [open, setOpen] = useState(false);
  const [mode, setMode] = useState<'question' | 'navigation'>('navigation');
  const [question, setQuestion] = useState('');
  const [navigationText, setNavigationText] = useState('');
  const [questionHint, setQuestionHint] = useState('');
  const [navigationHint, setNavigationHint] = useState('');
  const [reply, setReply] = useState<AssistantAnswer | null>(null);
  const [showCitation, setShowCitation] = useState(false);
  const [asking, setAsking] = useState(false);
  const askLock = useRef(false);

  const [pose, setPose] = useState<keyof typeof poses>('idle');
  const [facing, setFacing] = useState(1);
  const [bounds, setBounds] = useState<Bounds | null>(null);
  const overlay = useRef<ViewInstance>(null);
  const position = useRef(new Animated.ValueXY()).current;
  const current = useRef<Point | null>(null);
  const dragOrigin = useRef<Point | null>(null);
  const pointerOrigin = useRef<Point | null>(null);
  const dragging = useRef(false);
  const pressBecameDrag = useRef(false);
  const onArriveRef = useRef(onArrive);

  useEffect(() => {
    onArriveRef.current = onArrive;
  }, [onArrive]);

  useEffect(() => {
    const listener = position.addListener(point => { current.current = point; });
    return () => position.removeListener(listener);
  }, [position]);

  useEffect(() => {
    if (!bounds) return;
    position.stopAnimation();
    position.setValue(keepOnScreen(
      current.current ?? { x: 16, y: bounds.height - catSize - 16 },
      bounds,
    ));
  }, [bounds, position]);

  useEffect(() => {
    if (!destination || !bounds) {
      setPose('idle');
      return;
    }
    let cancelled = false;
    let movement: Animated.CompositeAnimation | undefined;
    overlay.current?.measureInWindow((left, top) => {
      if (cancelled || dragging.current || !current.current) return;
      const next = keepOnScreen({ x: destination.x - left, y: destination.y - top }, bounds);
      const dx = next.x - current.current.x;
      const distance = Math.hypot(dx, next.y - current.current.y);
      if (Math.abs(dx) > 4) setFacing(dx < 0 ? -1 : 1);
      setPose('run');
      movement = Animated.timing(position, {
        toValue: next, duration: Math.max(260, Math.min(950, distance * 2.3)), useNativeDriver: false,
      });
      movement.start(({ finished }) => {
        if (cancelled || !finished) return;
        setPose('paw');
        onArriveRef.current(destination.request);
      });
    });
    return () => {
      cancelled = true;
      movement?.stop();
    };
  }, [bounds, destination, position]);

  const pan = useMemo(() => Gesture.Pan()
    .minDistance(7)
    .maxPointers(1)
    .runOnJS(true)
    .onBegin(({ absoluteX, absoluteY }) => {
      pointerOrigin.current = { x: absoluteX, y: absoluteY };
    })
    .onStart(() => {
      dragging.current = true;
      pressBecameDrag.current = true;
      position.stopAnimation();
      dragOrigin.current = current.current;
      setPose('idle');
    })
    .onUpdate(({ absoluteX, absoluteY }) => {
      const origin = dragOrigin.current;
      const pointer = pointerOrigin.current;
      if (!bounds || !origin || !pointer) return;
      position.setValue(keepOnScreen({
        x: origin.x + absoluteX - pointer.x,
        y: origin.y + absoluteY - pointer.y,
      }, bounds));
    })
    .onFinalize(() => {
      dragOrigin.current = null;
      pointerOrigin.current = null;
      dragging.current = false;
    }), [bounds, position]);

  const askAdditive = async () => {
    if (askLock.current) return;
    setReply(null);
    setShowCitation(false);
    setQuestionHint('');
    const input = question.trim();
    if (!input) {
      setQuestionHint('請輸入想查的成分，例如「果膠是做什麼的？」');
      return;
    }
    if (Platform.OS !== 'web') {
      setQuestionHint('提問目前可在電腦的網頁預覽試用。');
      return;
    }
    askLock.current = true;
    setAsking(true);
    try {
      setReply(await sendAssistantQuestion(input));
    } catch {
      setQuestionHint('目前無法連到本機添加物助理，請稍後重試。');
    } finally {
      askLock.current = false;
      setAsking(false);
    }
  };

  const navigate = (command: string) => {
    const message = onNavigate(command);
    if (message) {
      setNavigationHint(message);
      return;
    }
    setOpen(false);
    setNavigationText('');
    setNavigationHint('');
  };

  return (
    <>
      <View
        ref={overlay}
        collapsable={false}
        pointerEvents="box-none"
        style={[styles.overlay, { top: insets.top, bottom: insets.bottom, left: insets.left, right: insets.right }]}
        onLayout={({ nativeEvent: { layout: { width, height } } }) => {
          setBounds(previous => previous?.width === width && previous.height === height ? previous : { width, height });
        }}
      >
        {bounds && (
          <GestureDetector gesture={pan} touchAction="none">
            <Animated.View style={[styles.cat, { transform: position.getTranslateTransform() }]}>
              <Pressable
                style={[styles.launch, pose !== 'idle' && styles.travelling]}
                accessibilityRole="button"
                accessibilityLabel="開啟貓咪導覽"
                accessibilityHint="可以拖動白貓"
                onPressIn={() => { pressBecameDrag.current = false; }}
                onPress={() => {
                  if (!pressBecameDrag.current) setOpen(true);
                }}
              >
                <View style={[styles.launchSprite, { transform: [{ translateY: -12 }, { scaleX: facing }] }]}>
                  <CatSprite key={pose} pose={pose} />
                </View>
              </Pressable>
            </Animated.View>
          </GestureDetector>
        )}
      </View>
      <Modal visible={open} transparent animationType="none" onRequestClose={() => setOpen(false)}>
        <View style={styles.backdrop}>
          <View style={styles.panel} accessibilityViewIsModal>
            <View style={styles.row}>
              <Text style={styles.title}>PackMeow 助理</Text>
              <Pressable accessibilityRole="button" onPress={() => setOpen(false)}>
                <Text style={styles.action}>關閉</Text>
              </Pressable>
            </View>
            <View style={styles.modePicker} accessibilityRole="tablist">
              {([{ id: 'question', label: '提問' }, { id: 'navigation', label: '介面導覽' }] as const)
                .map(item => (
                  <Pressable key={item.id} accessibilityRole="tab" accessibilityState={{ selected: mode === item.id }}
                    style={[styles.mode, mode === item.id && styles.modeSelected]} onPress={() => setMode(item.id)}>
                    <Text style={[styles.modeText, mode === item.id && styles.modeTextSelected]}>{item.label}</Text>
                  </Pressable>
                ))}
            </View>
            {mode === 'question' ? (
              <>
                <TextInput accessibilityLabel="輸入問題" placeholder="例如：果膠是做什麼的？"
                  placeholderTextColor="#64748b" value={question} onChangeText={setQuestion}
                  maxLength={120} returnKeyType="send" onSubmitEditing={() => void askAdditive()} style={styles.input} />
                {!!questionHint && <Text accessibilityLiveRegion="polite" style={styles.hint}>{questionHint}</Text>}
                {asking && <Text accessibilityLiveRegion="polite" style={styles.hint}>正在查本機添加物資料…</Text>}
                {reply && <View style={styles.reply}>
                  <Text accessibilityLiveRegion="polite" style={styles.replyText}>{reply.answer}</Text>
                  {reply.additive && (
                    <Pressable accessibilityRole="button" accessibilityState={{ expanded: showCitation }}
                      onPress={() => {
                        if (!reply.additive) return;
                        if (onOpenAdditive(reply.additive)) setOpen(false);
                        else setShowCitation(previous => !previous);
                      }}>
                      <Text style={styles.action}>查看添加物：{reply.additive.name}（INS {reply.additive.ins}）</Text>
                    </Pressable>
                  )}
                  {showCitation && reply.additive && (
                    <Text style={styles.replyText}>{reply.additive.summary}</Text>
                  )}
                  {reply.source && <Pressable accessibilityRole="link"
                    onPress={() => { if (reply.source) void Linking.openURL(reply.source.url).catch(() => setQuestionHint('無法開啟來源連結。')); }}>
                    <Text style={styles.action}>{reply.source.title}</Text>
                  </Pressable>}
                </View>}
                <Pressable accessibilityRole="button" disabled={asking} onPress={() => void askAdditive()}>
                  <Text style={styles.action}>送出問題</Text>
                </Pressable>
              </>
            ) : (
              <>
                <TextInput accessibilityLabel="輸入導覽指令" placeholder="例如：這個分數怎麼算？"
                  placeholderTextColor="#64748b" value={navigationText} onChangeText={setNavigationText}
                  maxLength={120} returnKeyType="go" onSubmitEditing={() => navigate(navigationText)} style={styles.input} />
                {!!navigationHint && <Text accessibilityLiveRegion="polite" style={styles.hint}>{navigationHint}</Text>}
                <View style={styles.shortcuts}>
                  {['看過敏原', '看營養明細', '看添加物', '看掃描紀錄', '我要掃條碼'].map(command => (
                    <Pressable key={command} accessibilityRole="button" style={styles.shortcut} onPress={() => navigate(command)}>
                      <Text style={styles.action}>{command}</Text>
                    </Pressable>
                  ))}
                </View>
                <Pressable accessibilityRole="button" onPress={() => navigate(navigationText)}>
                  <Text style={styles.action}>帶我去</Text>
                </Pressable>
              </>
            )}
          </View>
        </View>
      </Modal>
    </>
  );
}

const styles = StyleSheet.create({
  frame: { width: frameSize, height: frameSize, overflow: 'hidden' },
  sheet: { position: 'absolute', width: 512, height: 640 },
  overlay: { ...StyleSheet.absoluteFill, zIndex: 100 },
  cat: { position: 'absolute', top: 0, left: 0 },
  launch: {
    width: catSize, height: catSize, borderRadius: catSize / 2, backgroundColor: '#e2f5ef',
    borderWidth: 1, borderColor: '#8dc6b6', alignItems: 'center', justifyContent: 'center',
  },
  travelling: { backgroundColor: 'transparent', borderColor: 'transparent' },
  launchSprite: { alignItems: 'center', justifyContent: 'center' },
  backdrop: { flex: 1, justifyContent: 'center', backgroundColor: '#102b2760', padding: 20 },
  panel: { backgroundColor: '#fff', borderRadius: 18, padding: 16, gap: 12 },
  row: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', gap: 8 },
  title: { color: '#14352e', fontSize: 19, fontWeight: '700' },
  input: { color: '#14352e', borderWidth: 1, borderColor: '#8dc6b6', borderRadius: 8, padding: 12 },
  action: { color: '#145c50', fontSize: 15, fontWeight: '700', paddingVertical: 8 },
  modePicker: { flexDirection: 'row', backgroundColor: '#edf4f1', padding: 4, gap: 4, borderRadius: 10 },
  mode: { flex: 1, alignItems: 'center', padding: 9, borderRadius: 7 },
  modeSelected: { backgroundColor: '#145c50' },
  modeText: { color: '#145c50', fontWeight: '700' },
  modeTextSelected: { color: '#fff' },
  shortcuts: { flexDirection: 'row', flexWrap: 'wrap', gap: 8 },
  shortcut: { backgroundColor: '#e2f5ef', paddingHorizontal: 10, borderRadius: 8 },
  reply: { backgroundColor: '#f1f8f5', borderRadius: 10, padding: 12 },
  replyText: { color: '#14352e', fontSize: 15, lineHeight: 23 },
  hint: { color: '#475569' },
});
