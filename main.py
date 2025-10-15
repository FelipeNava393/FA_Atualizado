from __future__ import annotations

import os
import sys
import unicodedata
from typing import List, Optional

import pandas as pd
from PyQt6 import uic
from PyQt6.QtCore import QDate, QThread, Qt, QObject, pyqtSignal
from PyQt6.QtGui import QAction
from PyQt6.QtWidgets import (
    QApplication,
    QFileDialog,
    QLabel,
    QLineEdit,
    QListWidgetItem,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
    QMessageBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from carregar_dados import carregar_contrapartes, carregar_fa, get_cnpj


# --------------------- Threaded Workers ---------------------

class FADataLoaderWorker(QObject):
    fa_data_loaded = pyqtSignal(object)  # emits DataFrame or empty DataFrame

    def __init__(self, cnpj_filtro: Optional[List[str]], parent: Optional[QObject] = None):
        super().__init__(parent)
        self.cnpj_filtro = cnpj_filtro

    def run(self):
        try:
            df_fa = carregar_fa(cnpj_filtro=self.cnpj_filtro)
            self.fa_data_loaded.emit(df_fa)
        except Exception as e:
            print(f"Erro ao carregar dados FA em thread: {e}")
            self.fa_data_loaded.emit(pd.DataFrame())


class ContrapartesLoaderWorker(QObject):
    data_loaded = pyqtSignal(list)  # emits list[str]

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)

    def run(self):
        try:
            dados_contrapartes = carregar_contrapartes()
            self.data_loaded.emit(dados_contrapartes)
        except Exception as e:
            print(f"Erro ao carregar contrapartes: {e}")
            self.data_loaded.emit([])


# ----------------------- Main Window ------------------------

class MainWindow(QWidget):
    def __init__(self):
        super().__init__()
        uic.loadUi(os.path.join("templates", "template.ui"), self)

        # Styles
        self._apply_stylesheet()

        # Web view for Plotly
        self.web_view = QWebEngineView()
        self.grafico_layout = QVBoxLayout(self.grafico_placeholder)
        self.grafico_layout.setContentsMargins(0, 0, 0, 0)
        self.grafico_layout.addWidget(self.web_view)

        # Configure UI elements
        self.list_contrapartes.setSelectionMode(self.list_contrapartes.SelectionMode.MultiSelection)
        self.chart_selector.clear()
        self.chart_selector.addItems(["Dispersão", "Série Temporal", "Histograma", "Top N (média)"])

        # Signals
        self.aplicar_filtro_btn.clicked.connect(self.aplicar_filtro)
        self.limpar_filtro_btn.clicked.connect(self.limpar_filtro)
        self.select_all_btn.clicked.connect(self.select_all_contrapartes)
        self.clear_list_btn.clicked.connect(self.clear_selection)

        self.chart_selector.currentIndexChanged.connect(self.update_chart)
        self.search_input.textChanged.connect(self.apply_view_filters)
        self.date_from.dateChanged.connect(self.apply_view_filters)
        self.date_to.dateChanged.connect(self.apply_view_filters)
        self.export_csv_btn.clicked.connect(lambda: self.export_data("csv"))
        self.export_excel_btn.clicked.connect(lambda: self.export_data("xlsx"))

        # State
        self.df_fa: pd.DataFrame = pd.DataFrame()
        self.df_filtered: pd.DataFrame = pd.DataFrame()
        self._dates_initialized: bool = False

        self.list_contrapartes.addItem("Carregando contrapartes...")

        # Start thread to load contrapartes
        self._load_contrapartes_async()

    # ------------------- Setup helpers -------------------

    def _apply_stylesheet(self):
        css_path = os.path.join(os.getcwd(), "styles.qss")
        if os.path.exists(css_path):
            try:
                with open(css_path, "r", encoding="utf-8") as f:
                    self.setStyleSheet(f.read())
            except Exception:
                pass

    def _load_contrapartes_async(self):
        self.thread = QThread()
        self.worker = ContrapartesLoaderWorker()
        self.worker.moveToThread(self.thread)
        self.thread.started.connect(self.worker.run)
        self.worker.data_loaded.connect(self.update_contrapartes_ui)
        self.worker.data_loaded.connect(self.thread.quit)
        self.thread.start()

    # -------------------- Actions --------------------

    def aplicar_filtro(self):
        selected_items = self.list_contrapartes.selectedItems()
        selected_values = [item.text() for item in selected_items]

        if not selected_values:
            self._warn("Selecione uma Contraparte", "É preciso selecionar pelo menos uma empresa na lista para aplicar o filtro.")
            return

        self.label_condition_filtro.setText("Filtro aplicado")

        # Map fornecedores -> CNPJ
        cnpj_data = get_cnpj()
        fornecedor_to_cnpj = {fornecedor: cnpj for fornecedor, cnpj in cnpj_data}
        cnpj_selected = [fornecedor_to_cnpj.get(f, "") for f in selected_values if f in fornecedor_to_cnpj]

        self.fa_thread = QThread()
        self.fa_worker = FADataLoaderWorker(cnpj_filtro=cnpj_selected)
        self.fa_worker.moveToThread(self.fa_thread)
        self.fa_thread.started.connect(self.fa_worker.run)
        self.fa_worker.fa_data_loaded.connect(self.update_fa_ui)
        self.fa_worker.fa_data_loaded.connect(self.fa_thread.quit)
        self.fa_thread.start()

    def limpar_filtro(self):
        self.list_contrapartes.clearSelection()
        self.label_condition_filtro.setText("Nenhum filtro aplicado")
        self.df_fa = pd.DataFrame()
        self.df_filtered = pd.DataFrame()
        self._render_empty_table()
        self._render_empty_chart()
        self._update_kpis(pd.DataFrame())

    def select_all_contrapartes(self):
        self.list_contrapartes.selectAll()

    def clear_selection(self):
        self.list_contrapartes.clearSelection()

    # -------------------- UI updates --------------------

    def update_contrapartes_ui(self, dados_contrapartes: List[str]):
        self.list_contrapartes.clear()
        if not dados_contrapartes:
            self.list_contrapartes.addItem("(Nenhuma contraparte encontrada)")
            return
        # Apply search filter immediately if there is text
        query = self.search_input.text().strip().lower()
        for nome in dados_contrapartes:
            if query and query not in str(nome).lower():
                continue
            self.list_contrapartes.addItem(QListWidgetItem(str(nome)))

    def update_fa_ui(self, df_fa: pd.DataFrame):
        self.df_fa = df_fa.copy()
        # Initialize date pickers to dataset range (only once per load)
        try:
            date_col = self._first_present_column(
                self.df_fa, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]
            ) or "Início do Período"
            dates = pd.to_datetime(self.df_fa[date_col], errors="coerce")
            if not dates.dropna().empty:
                min_dt = dates.min()
                max_dt = dates.max()
                from_dt = QDate(min_dt.year, min_dt.month, min_dt.day)
                to_dt = QDate(max_dt.year, max_dt.month, max_dt.day)
                # Set valid ranges and defaults
                self.date_from.setDateRange(from_dt, to_dt)
                self.date_to.setDateRange(from_dt, to_dt)
                self.date_from.setDate(from_dt)
                self.date_to.setDate(to_dt)
                self._dates_initialized = True
        except Exception:
            self._dates_initialized = False

        self.apply_view_filters()

    def apply_view_filters(self):
        df = self.df_fa
        if df is None or df.empty:
            self.df_filtered = pd.DataFrame()
            self._render_empty_table()
            self._render_empty_chart()
            self._update_kpis(pd.DataFrame())
            return

        # Column helpers
        date_col = self._first_present_column(df, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]) or "Início do Período"
        fornecedor_col = self._first_present_column(df, ["FORNECEDOR", "Fornecedor", "Contraparte", "Empresa"]) or "FORNECEDOR"
        sigla_col = self._first_present_column(df, ["Sigla", "Ticker", "Código"]) or "Sigla"

        # Ensure datetime
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce", dayfirst=True)

        # Date range (only after pickers are initialized with dataset range)
        if self._dates_initialized:
            dt_from = self.date_from.date().toPyDate() if self.date_from.date().isValid() else None
            dt_to = self.date_to.date().toPyDate() if self.date_to.date().isValid() else None
            if dt_from:
                df = df[df[date_col] >= pd.Timestamp(dt_from)]
            if dt_to:
                df = df[df[date_col] <= pd.Timestamp(dt_to)]

        # Search filter (by fornecedor, sigla, cnpj)
        query = self.search_input.text().strip().lower()
        if query:
            cnpj_col = self._first_present_column(df, ["CNPJ"]) or "CNPJ"
            mask = (
                df[fornecedor_col].astype(str).str.lower().str.contains(query, na=False)
                | df[sigla_col].astype(str).str.lower().str.contains(query, na=False)
                | df[cnpj_col].astype(str).str.contains(query, na=False)
            )
            df = df[mask]

        self.df_filtered = df
        self.exibir_fa(df)
        self._update_kpis(df)
        self.update_chart()

    # -------------------- Table --------------------

    def _render_empty_table(self):
        self.fa_table.setRowCount(0)
        self.fa_table.setColumnCount(0)

    def exibir_fa(self, df: pd.DataFrame):
        self.fa_table.setSortingEnabled(False)
        self.fa_table.setRowCount(0)
        self.fa_table.setColumnCount(0)
        if df is None or df.empty:
            return

        self.fa_table.setColumnCount(len(df.columns))
        self.fa_table.setHorizontalHeaderLabels([str(c) for c in df.columns])
        self.fa_table.setRowCount(len(df))

        for i, row in enumerate(df.itertuples(index=False)):
            for j, value in enumerate(row):
                item = QTableWidgetItem("" if pd.isna(value) else str(value))
                self.fa_table.setItem(i, j, item)

        header = self.fa_table.horizontalHeader()
        header.setStretchLastSection(True)
        self.fa_table.resizeColumnsToContents()
        self.fa_table.setSortingEnabled(True)

    # -------------------- Charts --------------------

    def _render_empty_chart(self):
        self.web_view.setHtml("<h3 style='color:#c8c8c8;font-family:Inter,Segoe UI,Roboto,Arial'>Nenhum dado para exibir.</h3>")

    def update_chart(self):
        df = self.df_filtered
        if df is None or df.empty:
            self._render_empty_chart()
            return
        chart_type = self.chart_selector.currentText()
        if chart_type == "Dispersão":
            self._chart_scatter(df)
        elif chart_type == "Série Temporal":
            self._chart_time_series(df)
        elif chart_type == "Histograma":
            self._chart_histogram(df)
        elif chart_type == "Top N (média)":
            self._chart_top_n(df)
        else:
            self._chart_scatter(df)

    def _chart_scatter(self, df: pd.DataFrame):
        import plotly.express as px

        date_col = self._first_present_column(df, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]) or "Início do Período"
        y_col = self._first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
        color_col = self._first_present_column(df, ["Sigla", "FORNECEDOR"]) or "Sigla"

        fig = px.scatter(
            df,
            x=date_col,
            y=y_col,
            color=color_col,
            title="Fator de Alavancagem por Período",
            labels={date_col: "Início do Período", y_col: "Fator de Alavancagem (%)"},
            hover_data=[color_col, "CNPJ", y_col, date_col],
            template="plotly_dark",
        )
        fig.update_traces(mode="markers", marker=dict(size=8, opacity=0.8))
        fig.update_layout(legend_title_text=color_col)
        self._set_plotly_html(fig)

    def _chart_time_series(self, df: pd.DataFrame):
        import plotly.express as px

        date_col = self._first_present_column(df, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]) or "Início do Período"
        y_col = self._first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
        color_col = self._first_present_column(df, ["Sigla", "FORNECEDOR"]) or "Sigla"

        # Aggregate mean by date and color group to smooth noise
        grouped = df.groupby([date_col, color_col], as_index=False)[y_col].mean()
        fig = px.line(
            grouped,
            x=date_col,
            y=y_col,
            color=color_col,
            title="Série Temporal - Média por Período",
            labels={date_col: "Início do Período", y_col: "Fator de Alavancagem (%)"},
            template="plotly_dark",
        )
        fig.update_traces(mode="lines+markers")
        self._set_plotly_html(fig)

    def _chart_histogram(self, df: pd.DataFrame):
        import plotly.express as px

        y_col = self._first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
        fig = px.histogram(
            df,
            x=y_col,
            nbins=30,
            title="Distribuição do Fator de Alavancagem",
            labels={y_col: "Fator de Alavancagem (%)"},
            template="plotly_dark",
        )
        fig.update_layout(bargap=0.05)
        self._set_plotly_html(fig)

    def _chart_top_n(self, df: pd.DataFrame, n: int = 10):
        import plotly.express as px

        y_col = self._first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
        group_col = self._first_present_column(df, ["Sigla", "FORNECEDOR"]) or "Sigla"
        grouped = df.groupby(group_col, as_index=False)[y_col].mean().nlargest(n, y_col)
        fig = px.bar(
            grouped,
            x=group_col,
            y=y_col,
            title=f"Top {n} por Média de FA",
            labels={group_col: group_col, y_col: "Fator de Alavancagem (%)"},
            template="plotly_dark",
        )
        fig.update_layout(xaxis_tickangle=-30)
        self._set_plotly_html(fig)

    def _set_plotly_html(self, fig):
        html_content = fig.to_html(include_plotlyjs="cdn", full_html=False)
        self.web_view.setHtml(html_content)

    # -------------------- KPIs --------------------

    def _update_kpis(self, df: pd.DataFrame):
        def fmt(x: Optional[float]) -> str:
            return "-" if x is None or pd.isna(x) else f"{x:,.2f}%".replace(",", "X").replace(".", ",").replace("X", ".")

        y_col = self._first_present_column(df, ["Fator de Alavancagem", "FA", "Fator"]) or "Fator de Alavancagem"
        date_col = self._first_present_column(df, ["Início do Período", "Inicio do Período", "Inicio do Periodo", "Data"]) or "Início do Período"

        if df is None or df.empty or y_col not in df.columns:
            self.kpi_avg_value.setText("-")
            self.kpi_median_value.setText("-")
            self.kpi_min_value.setText("-")
            self.kpi_max_value.setText("-")
            self.kpi_count_value.setText("0")
            self.kpi_last_value.setText("-")
            return

        series = pd.to_numeric(df[y_col], errors="coerce")
        self.kpi_avg_value.setText(fmt(series.mean()))
        self.kpi_median_value.setText(fmt(series.median()))
        self.kpi_min_value.setText(fmt(series.min()))
        self.kpi_max_value.setText(fmt(series.max()))
        self.kpi_count_value.setText(str(int(series.count())))

        if date_col in df.columns:
            dates = pd.to_datetime(df[date_col], errors="coerce")
            if not dates.dropna().empty:
                last = dates.max()
                self.kpi_last_value.setText(last.strftime("%d/%m/%Y"))
            else:
                self.kpi_last_value.setText("-")
        else:
            self.kpi_last_value.setText("-")

    # -------------------- Export --------------------

    def export_data(self, fmt: str):
        df = self.df_filtered
        if df is None or df.empty:
            self._warn("Nada para exportar", "Aplique um filtro para gerar dados antes de exportar.")
            return
        suffix = "csv" if fmt == "csv" else "xlsx"
        path, _ = QFileDialog.getSaveFileName(self, "Salvar arquivo", f"dados_fa.{suffix}", f"*.{suffix}")
        if not path:
            return
        try:
            if fmt == "csv":
                df.to_csv(path, index=False)
            else:
                df.to_excel(path, index=False)
            self._info("Exportação concluída", f"Arquivo salvo em:\n{path}")
        except Exception as e:
            self._error("Falha ao exportar", str(e))

    # -------------------- Utils --------------------

    @staticmethod
    def _normalize_string(text: str) -> str:
        normalized = unicodedata.normalize("NFD", text)
        stripped = "".join(ch for ch in normalized if unicodedata.category(ch) != "Mn")
        return stripped.lower().strip()

    def _first_present_column(self, df: pd.DataFrame, candidates: List[str]) -> Optional[str]:
        normalized_map = {self._normalize_string(c): c for c in df.columns}
        for cand in candidates:
            key = self._normalize_string(cand)
            if key in normalized_map:
                return normalized_map[key]
        return None

    def _warn(self, title: str, description: str):
        self._show_message(QMessageBox.Icon.Warning, title, description)

    def _info(self, title: str, description: str):
        self._show_message(QMessageBox.Icon.Information, title, description)

    def _error(self, title: str, description: str):
        self._show_message(QMessageBox.Icon.Critical, title, description)

    def _show_message(self, icon: QMessageBox.Icon, title: str, description: str):
        msg = QMessageBox(self)
        msg.setIcon(icon)
        msg.setWindowTitle(title)
        msg.setText(title)
        msg.setInformativeText(description)
        msg.exec()


if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = MainWindow()
    window.setWindowTitle("Dashboard - Fator de Alavancagem")
    window.resize(1200, 800)
    window.show()
    sys.exit(app.exec())
