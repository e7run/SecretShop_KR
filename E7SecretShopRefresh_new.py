#import built-in
import tkinter as tk
from tkinter import ttk
from PIL import ImageTk, Image
import csv
import os
import time
import threading
from datetime import datetime
import re

#Library
import pyautogui
import pygetwindow as gw
import cv2
import numpy as np
import keyboard
from PIL import ImageGrab

class ShopItem:
    def __init__(self, path='', image=None, price=0, count=0):
        self.path = path
        self.image = image
        self.price = price
        self.count = count

    def __repr__(self):
        return f'ShopItem(path={self.path}, image={self.image}, price={self.price}, count={self.count})'


class RefreshStatistic:
    def __init__(self):
        self.refresh_count = 0
        self.items = {}
        self.start_time = datetime.now()

    def updateTime(self):
        self.start_time = datetime.now()

    def addShopItem(self, path: str, name='', price=0, count=0):
        image = cv2.imread(os.path.join('assets', path))
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        newItem = ShopItem(path, image, price, count)
        self.items[name] = newItem

    def getInventory(self):
        return self.items

    def getName(self):
        return list(self.items.keys())

    def getPath(self):
        return [shop_item.path for shop_item in self.items.values()]

    def getItemCount(self):
        return [shop_item.count for shop_item in self.items.values()]

    def getTotalCost(self):
        total = 0
        for shop_item in self.items.values():
            total += shop_item.price * shop_item.count
        return total

    def incrementRefreshCount(self):
        self.refresh_count += 1

    def writeToCSV(self):
        res_folder = 'ShopRefreshHistory'
        if not os.path.exists(res_folder):
            os.makedirs(res_folder)

        gen_path = 'refreshAttempt'
        for name in self.getName():
            gen_path += name[:4]
        gen_path += '.csv'

        path = os.path.join(res_folder, gen_path)

        if not os.path.isfile(path):
            with open(path, 'w', newline='') as file:
                writer = csv.writer(file)
                column_name = ['Time', 'Duration', 'Refresh count', 'Skystone spent', 'Gold spent']
                column_name.extend(self.getName())
                writer.writerow(column_name)
        with open(path, 'a', newline='') as file:
            writer = csv.writer(file)
            data = [self.start_time, datetime.now() - self.start_time,
                    self.refresh_count, self.refresh_count * 3, self.getTotalCost()]
            data.extend(self.getItemCount())
            writer.writerow(data)


class SecretShopRefresh:
    # 확인 팝업용 감지 임계값
    _POPUP_THRESHOLD = 0.75
    # poll 간격 / 최대 대기
    _POLL_INTERVAL   = 0.05
    _POLL_TIMEOUT    = 3.0
    # 스크롤 후 화면 안정 대기 (리프레시 대기와 분리)
    _SCROLL_SETTLE   = 0.15

    def __init__(self, title_name: str, callback=None, tk_instance: tk = None,
                 budget: int = None, allow_move: bool = False,
                 debug: bool = False, join_thread: bool = False):
        self.debug = debug
        self.loop_active = False
        self.loop_finish = True
        self.mouse_sleep = 0.3
        self.screenshot_sleep = 0.3
        self.callback = callback if callback else self.refreshFinishCallback
        self.budget = budget
        self.allow_move = allow_move
        self.join_thread = join_thread

        self.loading_asset = cv2.imread(os.path.join('assets', 'loading.jpg'))
        self.loading_asset = cv2.cvtColor(self.loading_asset, cv2.COLOR_BGR2GRAY)

        # 확인 팝업 감지용 — 팝업이 없어진 걸 확인하기 위해
        # 팝업 영역을 크롭해서 이전 프레임과 비교하는 방식 사용
        self._prev_confirm_crop = None

        self.title_name = title_name
        windows = gw.getWindowsWithTitle(self.title_name)
        self.window = next((w for w in windows if w.title == self.title_name), None)

        self.tk_instance = tk_instance
        self.rs_instance = RefreshStatistic()

    # ── 유틸: 확인 팝업 영역 크롭 ─────────────────────────────────────
    def _grab_confirm_region(self):
        """확인 버튼 근처 영역만 캡처 (전체 스크린샷 대비 훨씬 빠름)"""
        cx = int(self.window.left + self.window.width  * 0.55)
        cy = int(self.window.top  + self.window.height * 0.70)
        half_w, half_h = 80, 30
        region = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        img = ImageGrab.grab(bbox=region, all_screens=True)
        return cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)

    def _grab_refresh_confirm_region(self):
        """리프레시 확인 버튼 영역 크롭"""
        cx = int(self.window.left + self.window.width  * 0.58)
        cy = int(self.window.top  + self.window.height * 0.65)
        half_w, half_h = 80, 30
        region = (cx - half_w, cy - half_h, cx + half_w, cy + half_h)
        img = ImageGrab.grab(bbox=region, all_screens=True)
        return cv2.cvtColor(np.array(img), cv2.COLOR_BGR2GRAY)

    def _wait_until_changed(self, grab_fn, timeout: float = None):
        """
        grab_fn()이 반환하는 영역이 클릭 전과 달라질 때까지 poll.
        팝업이 닫히거나 화면이 전환되면 즉시 반환.
        timeout 초 이상 변화 없으면 그냥 반환 (안전장치).
        """
        if timeout is None:
            timeout = self._POLL_TIMEOUT
        before = grab_fn()
        deadline = time.perf_counter() + timeout
        while time.perf_counter() < deadline:
            time.sleep(self._POLL_INTERVAL)
            after = grab_fn()
            diff = cv2.absdiff(before, after)
            if diff.mean() > 2.0:   # 픽셀 평균 차이 2 이상이면 화면 변화
                return
        # timeout — 그냥 통과

    # ── Start shop refresh macro ──────────────────────────────────────
    def start(self):
        if self.loop_active or not self.loop_finish:
            return
        self.loop_active = True
        self.loop_finish = False
        keyboard_thread = threading.Thread(target=self.checkKeyPress)
        refresh_thread  = threading.Thread(target=self.shopRefreshLoop)
        keyboard_thread.daemon = True
        refresh_thread.daemon  = True
        keyboard_thread.start()
        refresh_thread.start()
        if self.join_thread:
            keyboard_thread.join()
            refresh_thread.join()

    def checkKeyPress(self):
        while self.loop_active and not self.loop_finish:
            self.loop_active = not keyboard.is_pressed('esc')
        self.loop_active = False
        print('Terminating shop refresh ...')

    def refreshFinishCallback(self):
        print('Terminated!')

    def shopRefreshLoop(self):
        try:
            if self.window.isMaximized or self.window.isMinimized:
                self.window.restore()
            if not self.allow_move:
                self.window.moveTo(0, 0)
            self.window.resizeTo(906, 539)
        except Exception as e:
            print(e)
            self.loop_active = False
            self.loop_finish = True
            self.callback()
            return

        mini_images = []
        hint, mini_labels, savings_labels = None, None, None
        if self.tk_instance:
            selected_path = self.rs_instance.getPath()
            for path in selected_path:
                img = Image.open(os.path.join('assets', path))
                img = img.resize((45, 45))
                img = ImageTk.PhotoImage(img)
                mini_images.append(img)
            hint, mini_labels, savings_labels = self.showMiniDisplays(mini_images)

        def _schedule_ui_update():
            if hint and hint.winfo_exists():
                hint.after(0, _do_ui_update)

        def _do_ui_update():
            if not (hint and hint.winfo_exists()):
                return
            inventory = self.rs_instance.getInventory()
            for (key, shop_item), count_lbl, sav_lbl in zip(
                    inventory.items(), mini_labels, savings_labels):
                count_lbl.config(text=str(shop_item.count))
              
        time.sleep(self.mouse_sleep)

        if not self.loop_active:
            if hint: hint.destroy()
            self.loop_finish = True
            self.callback()
            return

        try:
            try:
                self.window.activate()
            except Exception as e:
                print(e)

            self.rs_instance.updateTime()
            self.clickShop()
            time.sleep(1)

            # 리프레시 직후 첫 스캔은 아이템 슬라이드 인 대기 필요
            first_scan_sleep = max(0.7 + self.screenshot_sleep, 1.0)
            # 스크롤 후 두 번째 스캔은 짧은 안정화만 필요
            scroll_settle = self._SCROLL_SETTLE

            while self.loop_active:
                self.window.resizeTo(906, 539)
                brought = set()
                if not self.loop_active:
                    break

                # ── 상단 스캔 (리프레시 후 슬라이드 인 대기) ──────────
                time.sleep(first_scan_sleep)

                screenshot = self.takeScreenshot()
                process_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

                for key, shop_item in self.rs_instance.getInventory().items():
                    pos = self.findItemPosition(process_screenshot, shop_item.image)
                    if pos is not None:
                        self.clickBuy(pos)
                        if key == 'Covenant bookmark':
                            shop_item.count += 5
                        elif key == 'Mystic medal':
                            shop_item.count += 50
                        else:
                            shop_item.count += 1
                        brought.add(key)

                _schedule_ui_update()
                if not self.loop_active:
                    break

                # ── 스크롤 ────────────────────────────────────────────
                self.scrollShop()
                # 스크롤 후엔 짧은 안정화만 — poll로 화면 변화 감지
                time.sleep(scroll_settle)
                if not self.loop_active:
                    break

                # ── 하단 스캔 ─────────────────────────────────────────
                screenshot = self.takeScreenshot()
                process_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)

                for key, shop_item in self.rs_instance.getInventory().items():
                    if key in brought:
                        continue
                    pos = self.findItemPosition(process_screenshot, shop_item.image)
                    if pos is not None:
                        self.clickBuy(pos)
                        if key == 'Covenant bookmark':
                            shop_item.count += 5
                        elif key == 'Mystic medal':
                            shop_item.count += 50
                        else:
                            shop_item.count += 1

                _schedule_ui_update()
                if not self.loop_active:
                    break

                if self.budget:
                    if self.rs_instance.refresh_count >= self.budget // 3:
                        break

                # ── 리프레시 ──────────────────────────────────────────
                self.clickRefresh()
                self.rs_instance.incrementRefreshCount()
                # mouse_sleep: 리프레시 버튼 이동 후 짧은 딜레이
                time.sleep(self.mouse_sleep)
                if self.window.title != self.title_name:
                    break

        except Exception as e:
            print(e)
            if hint: hint.destroy()
            self.rs_instance.writeToCSV()
            self.loop_active = False
            self.loop_finish = True
            self.callback()
            return

        if hint: hint.destroy()
        self.rs_instance.writeToCSV()
        self.loop_active = False
        self.loop_finish = True
        self.callback()

    # ── 절약 하늘석 계산 ─────────────────────────────────────────────
    COV_PACK_STONES = 950
    COV_PACK_COUNT  = 50

   

    # ── 미니 디스플레이 ──────────────────────────────────────────────
    def showMiniDisplays(self, mini_images):
        C_BG     = '#FFFFFF'
        C_BORDER = '#E4E7ED'
        C_GOLD   = '#F59E0B'
        C_ACCENT = '#4F6EF7'
        C_GREEN  = '#10B981'

        if self.tk_instance is None:
            return None, None, None

        hint = tk.Toplevel(self.tk_instance)
        hint.geometry('240x%d+%d+%d' % (
            60 + len(mini_images) * 70,
            self.window.left,
            self.window.top + self.window.height))
        hint.title('구매 현황')
        try:
            hint.iconbitmap(os.path.join('assets', 'lo.ico'))
        except Exception:
            pass
        hint.config(bg=C_BG)
        hint.resizable(False, False)

        header = tk.Frame(hint, bg=C_ACCENT, padx=10, pady=6)
        header.pack(fill='x')
        tk.Label(header, text='ESC를 누르면 종료됩니다.',
                 font=('Segoe UI', 9), bg=C_ACCENT, fg='#FFFFFF').pack(anchor='w')

        mini_stats     = tk.Frame(hint, bg=C_BG, padx=10, pady=8)
        mini_labels    = []
        savings_labels = []

        inventory_names = list(self.rs_instance.getName())

        for i, img in enumerate(mini_images):
            key = inventory_names[i] if i < len(inventory_names) else ''
            row = tk.Frame(mini_stats, bg=C_BG, pady=4)
            tk.Label(row, image=img, bg=C_BG).pack(side=tk.LEFT)
            info = tk.Frame(row, bg=C_BG)
            info.pack(side=tk.LEFT, fill='x', expand=True, padx=(8, 0))
            count = tk.Label(info, text='0',
                             font=('Segoe UI Semibold', 14),
                             bg=C_BG, fg=C_GOLD, anchor='w')
            count.pack(anchor='w')
            mini_labels.append(count)
            if key == 'Covenant bookmark':
                sav = tk.Label(info, text='',
                               font=('Segoe UI', 8),
                               bg=C_BG, fg=C_GREEN, anchor='w')
                sav.pack(anchor='w')
                savings_labels.append(sav)
            else:
                savings_labels.append(None)
            row.pack(fill='x')
            tk.Frame(mini_stats, bg=C_BORDER, height=1).pack(fill='x')

        mini_stats.pack(fill='x')
        return hint, mini_labels, savings_labels

    def addShopItem(self, path: str, name='', price=0, count=0):
        self.rs_instance.addShopItem(path, name, price, count)

    def takeScreenshot(self):
        try:
            try:
                self.window.activate()
            except Exception as e:
                print(e)
            region = [self.window.left, self.window.top,
                      self.window.width, self.window.height]
            screenshot = ImageGrab.grab(
                bbox=(region[0], region[1],
                      region[2] + region[0], region[3] + region[1]),
                all_screens=True)
            return np.array(screenshot)
        except Exception as e:
            print(e)
            return None

    def checkLoading(self, process_screenshot):
        result = cv2.matchTemplate(process_screenshot, self.loading_asset, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= 0.75)
        if loc[0].size <= 0:
            return process_screenshot, False
        for _ in range(14):
            time.sleep(1)
            screenshot = self.takeScreenshot()
            process_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
            result = cv2.matchTemplate(process_screenshot, self.loading_asset, cv2.TM_CCOEFF_NORMED)
            loc = np.where(result >= 0.75)
            if loc[0].size <= 0:
                time.sleep(1.5)
                screenshot = self.takeScreenshot()
                process_screenshot = cv2.cvtColor(screenshot, cv2.COLOR_BGR2GRAY)
                return process_screenshot, True
        return None, False

    def findItemPosition(self, process_screenshot, process_item):
        result = cv2.matchTemplate(process_screenshot, process_item, cv2.TM_CCOEFF_NORMED)
        loc = np.where(result >= 0.75)

        if self.debug and loc[0].size > 0:
            debug_screenshot = cv2.cvtColor(process_screenshot.copy(), cv2.COLOR_GRAY2RGB)
            for pt in zip(*loc[::-1]):
                cv2.rectangle(debug_screenshot, pt,
                              (pt[0] + process_item.shape[1],
                               pt[1] + process_item.shape[0]),
                              (0, 255, 0), 1)
            cv2.imshow('Press any key to continue ...', debug_screenshot)
            cv2.waitKey(0)
            cv2.destroyAllWindows()
            time.sleep(1)
            self.window.activate()
            time.sleep(1)

        if loc[0].size > 0:
            x = self.window.left + self.window.width  * 0.90
            y = self.window.top  + loc[0][0] + self.window.height * 0.085
            return (x, y)
        return None

    # ── BUY MACRO ────────────────────────────────────────────────────
    def clickBuy(self, pos):
        if pos is None:
            return False
        x, y = pos
        pyautogui.moveTo(x, y)
        # interval을 0.05 s로 단축 — 더블클릭 인식엔 충분
        pyautogui.click(clicks=2, interval=min(0.05, self.mouse_sleep))
        time.sleep(self.mouse_sleep)
        self.clickConfirmBuy()
        return True

    def clickConfirmBuy(self):
        x = self.window.left + self.window.width  * 0.55
        y = self.window.top  + self.window.height * 0.70
        pyautogui.moveTo(x, y)
        pyautogui.click(clicks=2, interval=min(0.05, self.mouse_sleep))
        # 고정 sleep 제거 → 팝업이 실제로 사라질 때까지 poll
        self._wait_until_changed(self._grab_confirm_region,
                                 timeout=self._POLL_TIMEOUT)

    # ── REFRESH MACRO ─────────────────────────────────────────────────
    def clickRefresh(self):
        x = self.window.left + self.window.width  * 0.20
        y = self.window.top  + self.window.height * 0.90
        pyautogui.moveTo(x, y)
        pyautogui.click(clicks=2, interval=min(0.05, self.mouse_sleep))
        time.sleep(self.mouse_sleep)
        self.clickConfirmRefresh()

    def clickConfirmRefresh(self):
        x = self.window.left + self.window.width  * 0.58
        y = self.window.top  + self.window.height * 0.65
        pyautogui.moveTo(x, y)
        pyautogui.click(clicks=2, interval=min(0.05, self.mouse_sleep))
        # 리프레시 완료(상점 아이템 교체) 될 때까지 poll
        self._wait_until_changed(self._grab_refresh_confirm_region,
                                 timeout=self._POLL_TIMEOUT)

    # ── SHOP MACRO ───────────────────────────────────────────────────
    def clickShop(self):
        x = self.window.left + self.window.width  * 0.05
        y = self.window.top  + self.window.height * 0.41
        pyautogui.moveTo(x, y)
        pyautogui.click()
        time.sleep(self.mouse_sleep)

        x = self.window.left + self.window.width  * 0.44
        y = self.window.top  + self.window.height * 0.26
        pyautogui.moveTo(x, y)
        pyautogui.click()
        time.sleep(self.mouse_sleep)

        x = self.window.left + self.window.width  * 0.05
        y = self.window.top  + self.window.height * 0.41
        pyautogui.moveTo(x, y)
        pyautogui.click()

    def scrollShop(self):
        x = self.window.left + self.window.width  * 0.58
        y = self.window.top  + self.window.height * 0.65
        pyautogui.moveTo(x, y)
        time.sleep(0.1)
        pyautogui.mouseDown(button='left')
        time.sleep(0.1)
        pyautogui.moveTo(x, y - self.window.height * 0.277)
        pyautogui.mouseUp(button='left')

    def scrollUp(self):
        x = self.window.left + self.window.width  * 0.58
        y = self.window.top  + self.window.height * 0.65
        pyautogui.moveTo(x, y - self.window.height * 0.277)
        time.sleep(0.1)
        pyautogui.mouseDown(button='left')
        time.sleep(0.1)
        pyautogui.moveTo(x, y)
        pyautogui.mouseUp(button='left')


class AppConfig():
    def __init__(self):
        self.RECOGNIZE_TITLES = {'Epic Seven',
                                 'BlueStacks App Player',
                                 'LDPlayer',
                                 'MuMu Player 12',
                                 '에픽세븐',
                                 'Google Play Games on PC Emulator'}
        self.ALL_ITEMS = [['cov.png', 'Covenant bookmark', 184000],
                          ['mys.png', 'Mystic medal', 280000],
                          ['fb.png',  'Friendship bookmark', 18000]]
        self.MANDATORY_PATH = {'cov.png', 'mys.png'}
        self.DEBUG = False


class AutoRefreshGUI:
    def __init__(self):
        self.app_config = AppConfig()
        self.root = tk.Tk()

        self.C_BG        = '#F7F8FA'
        self.C_SURFACE   = '#FFFFFF'
        self.C_BORDER    = '#E4E7ED'
        self.C_PRIMARY   = '#4F6EF7'
        self.C_PRIMARY_H = '#3A57D7'
        self.C_TEXT      = '#1A1D23'
        self.C_MUTED     = '#6B7280'
        self.C_GOLD      = '#F59E0B'
        self.C_DISABLED  = '#D1D5DB'
        self.C_DIS_TEXT  = '#9CA3AF'
        self.FONT_BODY   = ('Segoe UI', 10)
        self.FONT_LABEL  = ('Segoe UI', 9)
        self.FONT_SMALL  = ('Segoe UI', 8)
        self.FONT_H1     = ('Segoe UI Semibold', 15)
        self.FONT_H2     = ('Segoe UI Semibold', 9)

        self.root.config(bg=self.C_BG)
        self.root.title('비상런')
        self.root.geometry('380x660')
        self.root.minsize(380, 660)
        self.root.resizable(False, False)
        try:
            self.root.iconbitmap(os.path.join('assets', 'lo.ico'))
        except Exception:
            pass

        self.title_name        = ''
        self.mouse_speed       = 0.3
        self.screenshot_speed  = 0.3
        self.ignore_path       = {'fb.png'}
        self.keep_image_open   = []
        self.lock_start_button = False
        self.budget            = ''

        style = ttk.Style(self.root)
        style.theme_use('clam')
        style.configure('TCombobox',
                        fieldbackground=self.C_SURFACE,
                        background=self.C_SURFACE,
                        foreground=self.C_TEXT,
                        bordercolor=self.C_BORDER,
                        lightcolor=self.C_BORDER,
                        darkcolor=self.C_BORDER,
                        arrowcolor=self.C_MUTED,
                        padding=6)
        style.map('TCombobox', fieldbackground=[('readonly', self.C_SURFACE)])

        outer = tk.Frame(self.root, bg=self.C_BG)
        outer.pack(fill='both', expand=True)
        canvas = tk.Canvas(outer, bg=self.C_BG, highlightthickness=0)
        canvas.pack(side='left', fill='both', expand=True)
        inner = tk.Frame(canvas, bg=self.C_BG)
        canvas_win = canvas.create_window((0, 0), window=inner, anchor='nw')

        def _on_frame_configure(e):
            canvas.configure(scrollregion=canvas.bbox('all'))
        def _on_canvas_configure(e):
            canvas.itemconfig(canvas_win, width=e.width)
        inner.bind('<Configure>', _on_frame_configure)
        canvas.bind('<Configure>', _on_canvas_configure)
        canvas.bind_all('<MouseWheel>',
                        lambda e: canvas.yview_scroll(int(-1*(e.delta/120)), 'units'))

        def make_card(parent, pady=(0, 8)):
            card = tk.Frame(parent, bg=self.C_SURFACE,
                            highlightbackground=self.C_BORDER,
                            highlightthickness=1,
                            padx=12, pady=10)
            card.pack(fill='x', padx=16, pady=pady)
            return card

        def section_label(parent, text, pady_top=8):
            tk.Label(parent, text=text,
                     font=self.FONT_H2,
                     bg=self.C_BG,
                     fg=self.C_MUTED).pack(anchor='w', padx=16, pady=(pady_top, 3))

        section_label(inner, '', pady_top=0)
        card1 = make_card(inner)
        tk.Label(card1, text='사용 중인 클라이언트 또는 에뮬레이터 선택',
                 font=self.FONT_LABEL, bg=self.C_SURFACE, fg=self.C_MUTED).pack(anchor='w', pady=(0, 4))

        def onSelect(event):
            t = titles_combo_box.get()
            if t not in gw.getAllTitles():
                self._set_start_btn_state(False); return
            self.title_name = t
            if not self.lock_start_button: self._set_start_btn_state(True)

        def onEnter(event):
            t = titles_combo_box.get()
            if t == '' or t not in gw.getAllTitles():
                self._set_start_btn_state(False); return
            self.title_name = t
            if not self.lock_start_button: self._set_start_btn_state(True)

        titles = sorted(self.app_config.RECOGNIZE_TITLES)
        titles_combo_box = ttk.Combobox(card1, values=titles, font=self.FONT_BODY, width=32)
        titles_combo_box.bind('<<ComboboxSelected>>', onSelect)
        titles_combo_box.bind('<KeyRelease>', onEnter)
        titles_combo_box.pack(fill='x')

        section_label(inner, '구매 아이템')
        card2 = make_card(inner)
        items_grid = tk.Frame(card2, bg=self.C_SURFACE)
        items_grid.pack(fill='x')
        self.item_rows_parent = items_grid

        for index, item in enumerate(self.app_config.ALL_ITEMS):
            self.keep_image_open.append(
                ImageTk.PhotoImage(Image.open(os.path.join('assets', item[0]))))
            self.packItem(index, item[0], item[1], item[2])

        section_label(inner, '설정')
        card3 = make_card(inner)

        def validateFloat(value, action):
            if action == '1':
                try:
                    return 0 <= float(value) <= 10
                except:
                    return False
            return True

        def validateInt(value):
            try:
                if value == '': return True
                return value.isdigit() and int(value) <= 100000000
            except:
                return False

        valid_float_reg = self.root.register(validateFloat)
        valid_int_reg   = self.root.register(validateInt)

        def make_compact_entry(parent, label_text, default=None, vcmd=None, col=0):
            cell = tk.Frame(parent, bg=self.C_SURFACE, padx=(0 if col == 0 else 6))
            cell.grid(row=0, column=col, sticky='ew',
                      padx=(0, 6 if col == 0 else 0))
            tk.Label(cell, text=label_text, font=('Segoe UI', 8),
                     bg=self.C_SURFACE, fg=self.C_MUTED, anchor='w').pack(anchor='w')
            ent = tk.Entry(cell, font=('Segoe UI', 10), width=6,
                           bg=self.C_BG, fg=self.C_TEXT,
                           relief='flat',
                           highlightbackground=self.C_BORDER,
                           highlightthickness=1,
                           insertbackground=self.C_TEXT)
            if default is not None: ent.insert(0, default)
            if vcmd: ent.config(validate='key', validatecommand=vcmd)
            ent.pack(fill='x', pady=(2, 0))
            return ent

        speed_grid = tk.Frame(card3, bg=self.C_SURFACE)
        speed_grid.pack(fill='x')
        speed_grid.columnconfigure(0, weight=1)
        speed_grid.columnconfigure(1, weight=1)
        speed_grid.columnconfigure(2, weight=1)

        self.mouse_speed_entry = make_compact_entry(
            speed_grid, '마우스 속도 (s)', self.mouse_speed,
            vcmd=(valid_float_reg, '%P', '%d'), col=0)
        self.screenshot_speed_entry = make_compact_entry(
            speed_grid, '스크린 인식 속도 (s)', self.screenshot_speed,
            vcmd=(valid_float_reg, '%P', '%d'), col=1)
        self.limit_spend_entry = make_compact_entry(
            speed_grid, '소비 하늘석 (기본: 전체)', None,
            vcmd=(valid_int_reg, '%P'), col=2)

        section_label(inner, '추가 옵션')
        card4 = make_card(inner)
        self.hint_cbv          = tk.BooleanVar(value=True)
        self.move_zerozero_cbv = tk.BooleanVar(value=True)

        def make_toggle_row(parent, text, var):
            row = tk.Frame(parent, bg=self.C_SURFACE, pady=3)
            row.pack(fill='x')
            tk.Label(row, text=text, font=self.FONT_LABEL,
                     bg=self.C_SURFACE, fg=self.C_TEXT).pack(side='left')
            cb = tk.Checkbutton(row, variable=var,
                                bg=self.C_SURFACE,
                                activebackground=self.C_SURFACE,
                                relief='flat', bd=0,
                                highlightthickness=0,
                                cursor='hand2')
            cb.select()
            cb.pack(side='right')
            return cb

        make_toggle_row(card4, '실시간 구매 정보 보기',      self.hint_cbv)
        make_toggle_row(card4, '에뮬레이터 위치 자동 조절', self.move_zerozero_cbv)

        btn_frame = tk.Frame(inner, bg=self.C_BG, pady=10)
        btn_frame.pack(fill='x', padx=16)
        self.start_button = tk.Button(
            btn_frame,
            text='구매 시작',
            font=('Segoe UI Semibold', 12),
            bg=self.C_PRIMARY, fg='#FFFFFF',
            activebackground=self.C_PRIMARY_H,
            activeforeground='#FFFFFF',
            disabledforeground=self.C_DIS_TEXT,
            relief='flat', bd=0,
            padx=0, pady=11,
            cursor='hand2',
            state=tk.DISABLED,
            command=self.startShopRefresh)
        self.start_button.pack(fill='x')

        def on_enter_btn(e):
            if str(self.start_button['state']) != 'disabled':
                self.start_button.config(bg=self.C_PRIMARY_H)
        def on_leave_btn(e):
            if str(self.start_button['state']) != 'disabled':
                self.start_button.config(bg=self.C_PRIMARY)
        self.start_button.bind('<Enter>', on_enter_btn)
        self.start_button.bind('<Leave>', on_leave_btn)

        tk.Label(inner,
                 text='ESC를 누르면 구매 매크로가 종료 됩니다.\n이 매크로는 Solunium의 코드를 사용했습니다.',
                 font=self.FONT_SMALL, bg=self.C_BG, fg=self.C_MUTED).pack(pady=(0, 12))

        if titles:
            for t in titles:
                if t in gw.getAllTitles():
                    self.title_name = t
                    titles_combo_box.set(t)
                    if not self.lock_start_button: self._set_start_btn_state(True)
                    break
        if not self.title_name:
            google_play_title_pattern = re.compile(r"^(Epic Seven|에픽세븐) - \w+$", re.UNICODE)
            for t in gw.getAllTitles():
                if google_play_title_pattern.fullmatch(t):
                    self.title_name = t
                    titles_combo_box.set(t)
                    if not self.lock_start_button: self._set_start_btn_state(True)
                    break

        self.root.mainloop()

    def _set_start_btn_state(self, enabled: bool):
        if enabled:
            self.start_button.config(state=tk.NORMAL,
                                     bg=self.C_PRIMARY, fg='#FFFFFF')
        else:
            self.start_button.config(state=tk.DISABLED,
                                     bg=self.C_DISABLED, fg=self.C_DIS_TEXT)

    def packItem(self, index, path, name='', price=0):
        ITEM_NAMES  = {'cov.png': '성약의 책갈피', 'mys.png': '신비의 메달',  'fb.png': '우정 포인트'}
        ITEM_PRICES = {'cov.png': '184,000 G',     'mys.png': '280,000 G',   'fb.png': '18,000 G'}

        def updateIgnore():
            if cbv.get() == 1: self.ignore_path.discard(path)
            else:              self.ignore_path.add(path)

        cbv = tk.IntVar()
        col_frame = tk.Frame(self.item_rows_parent, bg=self.C_SURFACE,
                             highlightbackground=self.C_BORDER,
                             highlightthickness=1,
                             padx=6, pady=8)
        col_frame.grid(row=0, column=index, sticky='nsew',
                       padx=(0, 5 if index < 2 else 0))
        self.item_rows_parent.columnconfigure(index, weight=1)

        img_frame = tk.Frame(col_frame, bg=self.C_GOLD, padx=2, pady=2)
        img_frame.pack()
        tk.Label(img_frame, image=self.keep_image_open[index], bg=self.C_GOLD).pack()

        tk.Label(col_frame, text=ITEM_NAMES.get(path, name or path),
                 font=('Segoe UI Semibold', 8),
                 bg=self.C_SURFACE, fg=self.C_TEXT, justify='center').pack(pady=(5, 0))
        tk.Label(col_frame,
                 text=ITEM_PRICES.get(path, f'{price:,} G' if price else ''),
                 font=('Segoe UI', 7),
                 bg=self.C_SURFACE, fg=self.C_MUTED).pack()

        cb = tk.Checkbutton(col_frame, variable=cbv, command=updateIgnore,
                            bg=self.C_SURFACE, activebackground=self.C_SURFACE,
                            relief='flat', bd=0, highlightthickness=0, cursor='hand2')
        if path in self.app_config.MANDATORY_PATH:
            cb.config(state=tk.DISABLED); cb.select()
        else:
            cb.config(state=tk.NORMAL)
        cb.pack(pady=(2, 0))

    def refreshComplete(self):
        print('Terminated!')
        self.root.title('비상런')
        self._set_start_btn_state(True)
        self.lock_start_button = False

    def startShopRefresh(self):
        self.root.title('비상런 — 실행 중(종료: ESC)')
        self.lock_start_button = True
        self._set_start_btn_state(False)
        self.ssr = SecretShopRefresh(
            title_name=self.title_name,
            callback=self.refreshComplete,
            debug=self.app_config.DEBUG)

        if self.hint_cbv.get():
            self.ssr.tk_instance = self.root
        if not self.move_zerozero_cbv.get():
            self.ssr.allow_move = True

        for item in self.app_config.ALL_ITEMS:
            if item[0] not in self.ignore_path:
                self.ssr.addShopItem(path=item[0], name=item[1], price=item[2])

        self.ssr.mouse_sleep = float(self.mouse_speed_entry.get()) \
            if self.mouse_speed_entry.get() != '' else self.mouse_speed
        self.ssr.screenshot_sleep = float(self.screenshot_speed_entry.get()) \
            if self.screenshot_speed_entry.get() != '' else self.screenshot_speed
        self.ssr.mouse_sleep       = max(0.01, self.ssr.mouse_sleep)
        self.ssr.screenshot_sleep  = max(0.01, self.ssr.screenshot_sleep)

        if self.limit_spend_entry.get() != '':
            self.ssr.budget = int(self.limit_spend_entry.get())

        print('refresh shop start!')
        print('Budget:',           self.ssr.budget)
        print('Mouse speed:',      self.ssr.mouse_sleep)
        print('Screenshot speed',  self.ssr.screenshot_sleep)
        if self.ssr.budget and self.ssr.budget >= 1000:
            ev_cost = 1691.04536   * int(self.ssr.budget) * 2
            ev_cov  = 0.006602509  * int(self.ssr.budget) * 2
            ev_mys  = 0.001700646  * int(self.ssr.budget) * 2
            print('Approximation based on budget:')
            print(f'Cost: {int(ev_cost):,}')
            print(f'Cov:  {ev_cov}')
            print(f'mys:  {ev_mys}')
        print()

        self.ssr.start()


if __name__ == '__main__':
    gui = AutoRefreshGUI()