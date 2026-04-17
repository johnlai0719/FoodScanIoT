import {
  useCssElement,
  useNativeVariable as useFunctionalVariable,
} from "react-native-css";
import React from "react";
import {
  View as RNView,
  Text as RNText,
  Pressable as RNPressable,
  ScrollView as RNScrollView,
  TextInput as RNTextInput,
  TouchableOpacity as RNTouchableOpacity,
} from "react-native";
import { CameraView as EXCameraView } from "expo-camera";

// CSS Variable hook
export const useCSSVariable =
  process.env.EXPO_OS !== "web"
    ? useFunctionalVariable
    : (variable: string) => `var(${variable})`;

// Helper to prevent 'undefined' className warnings in react-native-css
const withStableClassName = (props: any) => {
  return {
    ...props,
    className: props.className || "will-change-variable",
  };
};

// View
export type ViewProps = React.ComponentProps<typeof RNView> & {
  className?: string;
};
export const View = (props: ViewProps) => {
  return useCssElement(RNView, withStableClassName(props), { className: "style" });
};
View.displayName = "CSS(View)";

// Text
export const Text = (
  props: React.ComponentProps<typeof RNText> & { className?: string }
) => {
  return useCssElement(RNText, withStableClassName(props), { className: "style" });
};
Text.displayName = "CSS(Text)";

// ScrollView
export const ScrollView = (
  props: React.ComponentProps<typeof RNScrollView> & {
    className?: string;
    contentContainerClassName?: string;
  }
) => {
  const stableProps = {
    ...props,
    className: props.className || "will-change-variable",
    contentContainerClassName: props.contentContainerClassName || "will-change-variable",
  };
  return useCssElement(RNScrollView, stableProps, {
    className: "style",
    contentContainerClassName: "contentContainerStyle",
  });
};
ScrollView.displayName = "CSS(ScrollView)";

// Pressable
export const Pressable = (
  props: React.ComponentProps<typeof RNPressable> & { className?: string }
) => {
  return useCssElement(RNPressable, withStableClassName(props), { className: "style" });
};
Pressable.displayName = "CSS(Pressable)";

// TextInput
export const TextInput = (
  props: React.ComponentProps<typeof RNTextInput> & { className?: string }
) => {
  return useCssElement(RNTextInput, withStableClassName(props), { className: "style" });
};
TextInput.displayName = "CSS(TextInput)";

// TouchableOpacity
export const TouchableOpacity = (
  props: React.ComponentProps<typeof RNTouchableOpacity> & { className?: string }
) => {
  return useCssElement(RNTouchableOpacity, withStableClassName(props), { className: "style" });
};
TouchableOpacity.displayName = "CSS(TouchableOpacity)";

// CameraView
export const CameraView = (
  props: React.ComponentProps<typeof EXCameraView> & { className?: string }
) => {
  return useCssElement(EXCameraView, withStableClassName(props), { className: "style" });
};
CameraView.displayName = "CSS(CameraView)";
