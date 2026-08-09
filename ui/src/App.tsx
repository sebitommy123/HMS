import { Route, Routes } from "react-router-dom";

import { Layout } from "@/components/Layout";
import { CatalogDetail } from "@/pages/CatalogDetail";
import { Catalogs } from "@/pages/Catalogs";
import { ChatDetail } from "@/pages/ChatDetail";
import { Chats } from "@/pages/Chats";
import { Home } from "@/pages/Home";
import { DataSourceDetail } from "@/pages/DataSourceDetail";
import { NewCatalog } from "@/pages/NewCatalog";
import { NewFlexCatalog } from "@/pages/NewFlexCatalog";
import { ObjectFactoryDetail } from "@/pages/ObjectFactoryDetail";
import { ObjectTypeDetail } from "@/pages/ObjectTypeDetail";
import { ObjectTypes } from "@/pages/ObjectTypes";
import { Query } from "@/pages/Query";
import { RawTrinoQuery } from "@/pages/RawTrinoQuery";
import { State } from "@/pages/State";

export default function App() {
  return (
    <Routes>
      <Route element={<Layout />}>
        <Route index element={<Home />} />
        <Route path="catalogs" element={<Catalogs />} />
        <Route path="catalogs/new" element={<NewCatalog />} />
        <Route path="catalogs/new-flex" element={<NewFlexCatalog />} />
        <Route path="catalogs/:name" element={<CatalogDetail />} />
        <Route path="object-types" element={<ObjectTypes />} />
        <Route path="object-types/:id" element={<ObjectTypeDetail />} />
        <Route path="data-sources/:id" element={<DataSourceDetail />} />
        <Route path="object-factories/:id" element={<ObjectFactoryDetail />} />
        <Route path="state" element={<State />} />
        <Route path="query" element={<Query />} />
        <Route path="raw-trino-query" element={<RawTrinoQuery />} />
        <Route path="chats" element={<Chats />} />
        <Route path="chats/:id" element={<ChatDetail />} />
      </Route>
    </Routes>
  );
}
