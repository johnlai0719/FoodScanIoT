import React, { createContext, useContext, useState } from 'react';

type FontScaleContextType = {
  fontScale: number;
  toggleFontScale: () => void;
  isLarge: boolean;
};

const FontScaleContext = createContext<FontScaleContextType>({
  fontScale: 1,
  toggleFontScale: () => {},
  isLarge: false,
});

export function FontScaleProvider({ children }: { children: React.ReactNode }) {
  const [isLarge, setIsLarge] = useState(false);
  const fontScale = isLarge ? 1.2 : 1;
  const toggleFontScale = () => setIsLarge(v => !v);

  return (
    <FontScaleContext.Provider value={{ fontScale, toggleFontScale, isLarge }}>
      {children}
    </FontScaleContext.Provider>
  );
}

export const useFontScale = () => useContext(FontScaleContext);
